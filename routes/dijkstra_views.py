from decimal import Decimal
from collections import defaultdict, namedtuple
import heapq
import math

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

from drf_spectacular.utils import extend_schema

from .models import Punto, LineaRuta
from .serializers import (
    RutaPlanRequestSerializer,
    RutaPlanificadaSerializer,
)

Edge = namedtuple(
    'Edge',
    [
        'from_punto_id',
        'to_punto_id',
        'tiempo',
        'distancia',
        'linea_id',
        'linea_codigo',
        'linea_nombre',
        'linea_ruta_id',
        'numero_ruta',
        'es_caminar', 
    ],
)


class PlanificarRutaView(APIView):
    """
    Calcula la ruta más rápida entre dos coordenadas usando las paradas,
    con un máximo de 2 trasbordos (cambios de línea) y permitiendo caminatas
    al inicio, al final y entre paradas cercanas.
    """

    @extend_schema(
        operation_id="planear_ruta_por_coordenadas",
        summary="Ruta más corta con máximo 2 trasbordos (considerando caminata)",
        description=(
            "Recibe coordenadas de inicio y fin, construye un grafo con:\n"
            "- Tramos de bus (LineaRuta).\n"
            "- Caminatas desde el origen a paradas cercanas.\n"
            "- Caminatas entre paradas cercanas (trasbordos caminando).\n"
            "- Caminatas desde paradas cercanas hasta el destino.\n\n"
            "Utiliza Dijkstra con el tiempo como peso, penaliza las caminatas "
            "(tiempo más alto que el bus), penaliza los trasbordos y limita a "
            "2 cambios de línea."
        ),
        request=RutaPlanRequestSerializer,
        responses={200: RutaPlanificadaSerializer},
    )
    def post(self, request):
        req_serializer = RutaPlanRequestSerializer(data=request.data)
        req_serializer.is_valid(raise_exception=True)
        data = req_serializer.validated_data

        inicio = data["inicio"]
        fin = data["fin"]

        print("\n[PLANIFICAR] =====================")
        print(f"[PLANIFICAR] Inicio raw: {inicio}")
        print(f"[PLANIFICAR] Fin raw:    {fin}")

        inicio_lat = float(inicio["lat"])
        inicio_lon = float(inicio["lon"])
        fin_lat = float(fin["lat"])
        fin_lon = float(fin["lon"])

        puntos = list(Punto.objects.all())
        print(f"[PLANIFICAR] Puntos totales en BD: {len(puntos)}")

        if not puntos:
            return Response(
                {"detail": "No hay puntos cargados en el sistema."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        def distancia_cuadrada(lat1, lon1, lat2, lon2):
            return (lat1 - lat2) ** 2 + (lon1 - lon2) ** 2

        DEG_TO_KM = 111.0  # aproximación: 1° ~ 111 km
        WALK_SPEED_KMH = 4.0  # velocidad de caminata
        WALK_PENALTY_FACTOR = Decimal("3.0")  # penalizar caminar
        # Penalización extra por trasbordo (en minutos)
        TRANSFER_PENALTY_MIN = Decimal("8")

        # radio aprox en grados^2
        # ~400 m para trasbordos caminando
        MAX_TRANSFER_WALK_D2 = 1.5e-5
        # ~1 km para caminar desde parada al destino
        MAX_DEST_WALK_D2 = 9e-5
        # número máximo de paradas cercanas al origen para crear aristas de caminata
        MAX_ORIGIN_NEIGHBORS = 10

        INF = Decimal("999999999")

        def tiempo_desde_dist_km(dist_km: float) -> Decimal:
            """
            Convierte una distancia en km a tiempo de caminata en minutos,
            aplicando penalización para preferir bus cuando sea posible.
            """
            if dist_km <= 0:
                return Decimal("0")
            walk_min = (dist_km / WALK_SPEED_KMH) * 60.0
            walk_min *= float(WALK_PENALTY_FACTOR)
            return Decimal(str(walk_min))

        def describir_punto(p):
            return p.descripcion or f"{p.latitud},{p.longitud}"

        def puntos_mas_cercanos_debug(lat, lon, pool, k=10, label=""):
            """
            Sólo para log: imprime los k puntos más cercanos en 'pool'.
            """
            puntos_ordenados = sorted(
                pool,
                key=lambda p: distancia_cuadrada(
                    float(p.latitud),
                    float(p.longitud),
                    lat,
                    lon,
                ),
            )
            seleccion = puntos_ordenados[:k]

            print(
                f"[PLANIFICAR] k={k} puntos más cercanos a ({lat},{lon}) "
                f"en pool='{label}' (size={len(pool)}):"
            )
            for p in seleccion:
                d2 = distancia_cuadrada(
                    float(p.latitud),
                    float(p.longitud),
                    lat,
                    lon,
                )
                print(
                    f"   id={p.id} desc={p.descripcion} "
                    f"lat={p.latitud} lon={p.longitud} d2={d2}"
                )

            return seleccion

        edges = defaultdict(list)

        lineas_ruta = LineaRuta.objects.select_related("linea").all()
        print(f"[GRAFO] LineasRuta totales: {lineas_ruta.count()}")

        for lr in lineas_ruta:
            lps = list(
                lr.puntos.select_related("punto").order_by("orden")
            )
            print(
                f"[GRAFO] LineaRuta id={lr.id} linea={lr.linea.codigo} "
                f"sentido={lr.numero_ruta} puntos={len(lps)}"
            )

            for i in range(1, len(lps)):
                prev_lp = lps[i - 1]
                curr_lp = lps[i]

                edge = Edge(
                    from_punto_id=prev_lp.punto_id,
                    to_punto_id=curr_lp.punto_id,
                    tiempo=curr_lp.tiempo,
                    distancia=curr_lp.distancia,
                    linea_id=lr.linea_id,
                    linea_codigo=lr.linea.codigo,
                    linea_nombre=lr.linea.nombre,
                    linea_ruta_id=lr.id,
                    numero_ruta=lr.numero_ruta,
                    es_caminar=False,
                )
                edges[prev_lp.punto_id].append(edge)

        total_edges_bus = sum(len(v) for v in edges.values())
        print(f"[GRAFO] Aristas de BUS totales: {total_edges_bus}")

        if not edges:
            return Response(
                {"detail": "No hay tramos cargados en el sistema."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        puntos_salida_ids = set(edges.keys())
        puntos_salida = [p for p in puntos if p.id in puntos_salida_ids]
        print(
            f"[GRAFO] Puntos con al menos una salida (posibles inicios reales): "
            f"{len(puntos_salida)}"
        )

        puntos_bus_ids = set()
        for from_id, lst in edges.items():
            puntos_bus_ids.add(from_id)
            for e in lst:
                puntos_bus_ids.add(e.to_punto_id)

        puntos_bus = [p for p in puntos if p.id in puntos_bus_ids]
        print(
            f"[GRAFO] Puntos que pertenecen a alguna línea de bus "
            f"(para caminatas/trasbordos): {len(puntos_bus)}"
        )

        puntos_by_id = {p.id: p for p in puntos}

        candidatos_inicio = puntos_mas_cercanos_debug(
            inicio_lat,
            inicio_lon,
            pool=puntos_salida,
            k=MAX_ORIGIN_NEIGHBORS,
            label="salida",
        )
        candidatos_fin_debug = puntos_mas_cercanos_debug(
            fin_lat,
            fin_lon,
            pool=puntos,
            k=10,
            label="todos",
        )

        print(
            "[PLANIFICAR] IDs candidatos inicio (con salida):",
            [p.id for p in candidatos_inicio],
        )
        print(
            "[PLANIFICAR] IDs candidatos fin (debug):",
            [p.id for p in candidatos_fin_debug],
        )

        for ci in candidatos_inicio:
            print(
                f"[GRAFO] Edges desde candidato inicio id={ci.id} "
                f"({describir_punto(ci)}): {len(edges.get(ci.id, []))}"
            )


        ORIGIN_NODE_ID = 0
        DEST_NODE_ID = -1

        class PuntoVirtual:
            def __init__(self, id_, lat, lon, descripcion):
                self.id = id_
                self.latitud = Decimal(str(lat))
                self.longitud = Decimal(str(lon))
                self.descripcion = descripcion

        p_origen = PuntoVirtual(ORIGIN_NODE_ID, inicio_lat, inicio_lon, "Origen")
        p_destino = PuntoVirtual(DEST_NODE_ID, fin_lat, fin_lon, "Destino")

        puntos_by_id[ORIGIN_NODE_ID] = p_origen
        puntos_by_id[DEST_NODE_ID] = p_destino

        origin_walk_edges = 0
        for p in candidatos_inicio:
            lat_p = float(p.latitud)
            lon_p = float(p.longitud)
            dlat = inicio_lat - lat_p
            dlon = inicio_lon - lon_p
            d2 = dlat * dlat + dlon * dlon
            dist_deg = math.sqrt(d2)
            dist_km = dist_deg * DEG_TO_KM

            t_min = tiempo_desde_dist_km(dist_km)
            edge = Edge(
                from_punto_id=ORIGIN_NODE_ID,
                to_punto_id=p.id,
                tiempo=t_min,
                distancia=Decimal(str(dist_km)),
                linea_id=None,
                linea_codigo="CAMINAR_ORIGEN",
                linea_nombre="Caminata desde origen",
                linea_ruta_id=None,
                numero_ruta=0,
                es_caminar=True,
            )
            edges[ORIGIN_NODE_ID].append(edge)
            origin_walk_edges += 1

        print(f"[CAMINAR] Aristas ORIGEN -> parada: {origin_walk_edges}")

        walk_transfer_edges = 0
        n_transfer = len(puntos_bus)

        bus_lat = [float(p.latitud) for p in puntos_bus]
        bus_lon = [float(p.longitud) for p in puntos_bus]

        for i in range(n_transfer):
            p_i = puntos_bus[i]
            lat_i = bus_lat[i]
            lon_i = bus_lon[i]

            for j in range(i + 1, n_transfer):
                p_j = puntos_bus[j]
                lat_j = bus_lat[j]
                lon_j = bus_lon[j]

                dlat = lat_i - lat_j
                dlon = lon_i - lon_j
                d2 = dlat * dlat + dlon * dlon
                if d2 > MAX_TRANSFER_WALK_D2:
                    continue

                dist_deg = math.sqrt(d2)
                dist_km = dist_deg * DEG_TO_KM
                t_min = tiempo_desde_dist_km(dist_km)
                dist_dec = Decimal(str(dist_km))

                e_ij = Edge(
                    from_punto_id=p_i.id,
                    to_punto_id=p_j.id,
                    tiempo=t_min,
                    distancia=dist_dec,
                    linea_id=None,
                    linea_codigo="CAMINAR",
                    linea_nombre="Caminata entre paradas",
                    linea_ruta_id=None,
                    numero_ruta=0,
                    es_caminar=True,
                )
                e_ji = Edge(
                    from_punto_id=p_j.id,
                    to_punto_id=p_i.id,
                    tiempo=t_min,
                    distancia=dist_dec,
                    linea_id=None,
                    linea_codigo="CAMINAR",
                    linea_nombre="Caminata entre paradas",
                    linea_ruta_id=None,
                    numero_ruta=0,
                    es_caminar=True,
                )
                edges[p_i.id].append(e_ij)
                edges[p_j.id].append(e_ji)
                walk_transfer_edges += 2 

        print(f"[CAMINAR] Aristas caminando entre paradas: {walk_transfer_edges}")

        dest_walk_edges = 0
        for idx, p in enumerate(puntos_bus):
            lat_p = bus_lat[idx]
            lon_p = bus_lon[idx]
            dlat = lat_p - fin_lat
            dlon = lon_p - fin_lon
            d2 = dlat * dlat + dlon * dlon
            if d2 > MAX_DEST_WALK_D2:
                continue

            dist_deg = math.sqrt(d2)
            dist_km = dist_deg * DEG_TO_KM
            t_min = tiempo_desde_dist_km(dist_km)
            edge = Edge(
                from_punto_id=p.id,
                to_punto_id=DEST_NODE_ID,
                tiempo=t_min,
                distancia=Decimal(str(dist_km)),
                linea_id=None,
                linea_codigo="CAMINAR_DESTINO",
                linea_nombre="Caminata al destino",
                linea_ruta_id=None,
                numero_ruta=0,
                es_caminar=True,
            )
            edges[p.id].append(edge)
            dest_walk_edges += 1

        print(f"[CAMINAR] Aristas parada -> DESTINO: {dest_walk_edges}")

        total_edges_final = sum(len(v) for v in edges.values())
        print(
            f"[GRAFO] Aristas totales (incluyendo caminatas): {total_edges_final} "
            f"(bus={total_edges_bus}, origen={origin_walk_edges}, "
            f"transfer={walk_transfer_edges}, destino={dest_walk_edges})"
        )

        max_trasbordos = 2

        dist = {}
        prev_state = {}
        pq = []

        start_state = (ORIGIN_NODE_ID, None, 0) 
        dist[start_state] = Decimal("0")
        heapq.heappush(pq, (Decimal("0"), ORIGIN_NODE_ID, None, 0))
        print(f"[DIJKSTRA] Estado inicial: punto={ORIGIN_NODE_ID}, linea=None, trasbordos=0")

        best_state = None
        debug_steps = 0
        max_debug_steps = 300 

        while pq:
            cost, punto_id, linea_id, trasbordos = heapq.heappop(pq)
            state = (punto_id, linea_id, trasbordos)

            if debug_steps < max_debug_steps:
                print(
                    f"[DIJKSTRA] POP cost={cost} punto={punto_id} "
                    f"linea={linea_id} trasbordos={trasbordos}"
                )

            if dist.get(state, None) is not None and cost > dist[state]:
                if debug_steps < max_debug_steps:
                    print("   [DIJKSTRA] Estado desactualizado, se omite.")
                continue

            if punto_id == DEST_NODE_ID:
                best_state = state
                print(
                    f"[DIJKSTRA] >>> Alcanzado nodo DESTINO (id={DEST_NODE_ID}) "
                    f"con costo={cost} trasbordos={trasbordos}"
                )
                break

            edges_punto = edges.get(punto_id, [])
            for edge in edges_punto:
                if edge.es_caminar:
                    edge_linea = linea_id
                else:
                    edge_linea = edge.linea_id

                nuevo_trasbordos = trasbordos
                nueva_linea = linea_id
                transfer_penalty = Decimal("0")

                if edge_linea is not None:
                    if nueva_linea is None:
                        nueva_linea = edge_linea
                    elif nueva_linea != edge_linea:
                        nuevo_trasbordos = trasbordos + 1
                        nueva_linea = edge_linea
                        transfer_penalty = TRANSFER_PENALTY_MIN

                if nuevo_trasbordos > max_trasbordos:
                    if debug_steps < max_debug_steps:
                        print(
                            f"   [DIJKSTRA] Salto a {edge.to_punto_id} por "
                            f"linea={edge.linea_codigo} descartado: "
                            f"trasbordos {nuevo_trasbordos} > {max_trasbordos}"
                        )
                    continue

                nuevo_cost = cost + edge.tiempo + transfer_penalty
                next_state = (edge.to_punto_id, nueva_linea, nuevo_trasbordos)

                if nuevo_cost < dist.get(next_state, INF):
                    dist[next_state] = nuevo_cost
                    prev_state[next_state] = (state, edge)
                    heapq.heappush(
                        pq,
                        (nuevo_cost, edge.to_punto_id, nueva_linea, nuevo_trasbordos),
                    )
                    if debug_steps < max_debug_steps:
                        print(
                            f"   [DIJKSTRA] PUSH -> punto={edge.to_punto_id} "
                            f"linea={nueva_linea} ({edge.linea_codigo}) "
                            f"trasbordos={nuevo_trasbordos} "
                            f"cost={nuevo_cost} (penalizacion_trasbordo={transfer_penalty})"
                        )

            debug_steps += 1

        print(f"[DIJKSTRA] Estados totales visitados: {len(dist)}")

        if best_state is None:
            print("[DIJKSTRA] *** NO SE ENCONTRÓ RUTA AL DESTINO ***")
            return Response(
                {
                    "detail": "No se encontró una ruta entre los puntos con un máximo de 2 trasbordos."
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        print(f"[DIJKSTRA] best_state final: {best_state}, costo={dist[best_state]}")

        segmentos = []
        path_states = [best_state]

        state = best_state
        while state in prev_state:
            prev_s, edge = prev_state[state]
            segmentos.append(edge)
            path_states.append(prev_s)
            state = prev_s

        segmentos.reverse()
        path_states.reverse()

        print(f"[RUTA] Segmentos en la ruta final: {len(segmentos)}")
        for i, seg in enumerate(segmentos[:50]):
            print(
                f"   [RUTA] {i} "
                f"{seg.from_punto_id} -> {seg.to_punto_id} "
                f"linea={seg.linea_codigo} sentido={seg.numero_ruta} "
                f"tiempo={seg.tiempo} dist={seg.distancia} "
                f"caminar={seg.es_caminar}"
            )
        if len(segmentos) > 50:
            print(f"   [RUTA] ... ({len(segmentos) - 50} segmentos más)")

        node_ids = [st[0] for st in path_states]
        print(f"[RUTA] Nodos en la ruta: {node_ids}")

        num_nodos = len(node_ids)

        linea_trasbordo_flags = [None] * num_nodos

        for i in range(1, num_nodos - 1):
            prev_line = path_states[i - 1][1]
            curr_line = path_states[i][1]

            if prev_line is None:
                continue

            if prev_line != curr_line and curr_line is not None:
                incoming_edge = segmentos[i - 1]
                if not incoming_edge.es_caminar:
                    linea_trasbordo_flags[i] = incoming_edge.linea_codigo

        print("[RUTA] Flags de trasbordo por nodo:", linea_trasbordo_flags)

        tiempo_acumulado = Decimal("0")
        distancia_acumulada = Decimal("0")
        puntos_data = []
        orden = 1

        for idx, punto_id in enumerate(node_ids):
            p = puntos_by_id[punto_id]

            if idx == 0:
                puntos_data.append(
                    {
                        "orden": orden,
                        "latitud": p.latitud,
                        "longitud": p.longitud,
                        "descripcion": p.descripcion,
                        "distancia_tramo": Decimal("0"),
                        "distancia_acumulada": distancia_acumulada,
                        "tiempo_tramo": Decimal("0"),
                        "tiempo_acumulado": tiempo_acumulado,
                        "linea_trasbordo": linea_trasbordo_flags[idx],
                    }
                )
                orden += 1
                continue

            edge = segmentos[idx - 1]
            tiempo_acumulado += edge.tiempo
            distancia_acumulada += edge.distancia

            puntos_data.append(
                {
                    "orden": orden,
                    "latitud": p.latitud,
                    "longitud": p.longitud,
                    "descripcion": p.descripcion,
                    "distancia_tramo": edge.distancia,
                    "distancia_acumulada": distancia_acumulada,
                    "tiempo_tramo": edge.tiempo,
                    "tiempo_acumulado": tiempo_acumulado,
                    "linea_trasbordo": linea_trasbordo_flags[idx],
                }
            )
            orden += 1

        if tiempo_acumulado != dist[best_state]:
            print(
                f"[RUTA][WARN] tiempo_acumulado={tiempo_acumulado} "
                f"!= costo_best={dist[best_state]}"
            )

        lineas_usadas_map = {}
        for edge in segmentos:
            if edge.es_caminar:
                continue
            key = (edge.linea_id, edge.numero_ruta)
            if key not in lineas_usadas_map:
                lineas_usadas_map[key] = {
                    "codigo": edge.linea_codigo,
                    "nombre": edge.linea_nombre,
                    "sentido": edge.numero_ruta,
                }
        lineas_usadas = list(lineas_usadas_map.values())
        print("[RUTA] Líneas de BUS usadas en el trayecto:", lineas_usadas)

        if lineas_usadas:
            detalles = [
                f"{l['codigo']} (sentido {l['sentido']})"
                for l in lineas_usadas
            ]
            if len(detalles) == 1:
                texto_lineas_simple = f"utilizando la línea {detalles[0]}"
            else:
                texto_lineas_simple = (
                    "utilizando las líneas "
                    + ", ".join(detalles[:-1])
                    + f" y {detalles[-1]}"
                )
        else:
            texto_lineas_simple = "sin utilizar líneas"

        inicio_punto_real = puntos_by_id[node_ids[0]]
        fin_punto_real = puntos_by_id[node_ids[-1]]

        descripcion_ruta = (
            f"Ruta desde {describir_punto(inicio_punto_real)} "
            f"hasta {describir_punto(fin_punto_real)}, "
            f"{texto_lineas_simple} "
            f"y como máximo {max_trasbordos} trasbordos."
        )

        print("[RUTA] Descripción generada:", descripcion_ruta)

        payload = {
            "lineas": lineas_usadas,
            "descripcion_ruta": descripcion_ruta,
            "distancia_total": distancia_acumulada,
            "tiempo_total": tiempo_acumulado,
            "puntos": puntos_data,
        }

        resp_serializer = RutaPlanificadaSerializer(instance=payload)
        return Response(resp_serializer.data, status=status.HTTP_200_OK)
