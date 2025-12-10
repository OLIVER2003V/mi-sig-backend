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
    MultipleRutasSerializer,
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
        'linea_codigo_base',  # código sin sufijos (ej: "18" en lugar de "18 IDA")
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
        summary="Rutas alternativas con máximo 2 trasbordos (considerando caminata)",
        description=(
            "Recibe coordenadas de inicio y fin, construye un grafo con:\n"
            "- Tramos de bus (LineaRuta).\n"
            "- Caminatas desde el origen a paradas cercanas.\n"
            "- Caminatas entre paradas cercanas (trasbordos caminando).\n"
            "- Caminatas desde paradas cercanas hasta el destino.\n\n"
            "Utiliza Dijkstra con el tiempo como peso, penaliza las caminatas "
            "(tiempo más alto que el bus), penaliza los trasbordos y limita a "
            "2 cambios de línea.\n\n"
            "Devuelve hasta 3 rutas alternativas ordenadas por tiempo."
        ),
        request=RutaPlanRequestSerializer,
        responses={200: MultipleRutasSerializer},
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
        WALK_SPEED_KMH = 5.0  # velocidad de caminata (aumentada a 5 km/h, más realista)
        WALK_DISTANCE_FACTOR = 1.4  # factor para convertir distancia en línea recta a distancia por calles
        WALK_PENALTY_FACTOR = Decimal("1.5")  # penalizar caminar (reducido de 3.0 a 1.5)
        # Penalización extra por trasbordo (en minutos)
        TRANSFER_PENALTY_MIN = Decimal("8")

        # radio aprox en grados^2
        # ~400 m para trasbordos caminando entre paradas
        MAX_TRANSFER_WALK_D2 = 1.5e-5
        # ~1.5 km para caminar desde parada al destino (aumentado para evitar trasbordos innecesarios)
        MAX_DEST_WALK_D2 = 2e-4  # Aumentado de 9e-5 para permitir más caminata al final
        # número máximo de paradas cercanas al origen para crear aristas de caminata
        MAX_ORIGIN_NEIGHBORS = 10

        INF = Decimal("999999999")

        def tiempo_desde_dist_km(dist_km: float) -> Decimal:
            """
            Convierte una distancia en km a tiempo de caminata en minutos,
            aplicando:
            1. Factor para compensar que la distancia real por calles es mayor que línea recta
            2. Penalización moderada para preferir bus cuando sea posible.
            """
            if dist_km <= 0:
                return Decimal("0")
            # Ajustar distancia para simular caminar por calles (no línea recta)
            dist_km_real = dist_km * WALK_DISTANCE_FACTOR
            # Calcular tiempo real de caminata
            walk_min = (dist_km_real / WALK_SPEED_KMH) * 60.0
            # Aplicar penalización moderada
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

        def extraer_codigo_base(codigo):
            """
            Extrae el código base de una línea, removiendo sufijos como IDA, VUELTA, etc.
            Ejemplos: "18" -> "18", "18 IDA" -> "18", "18 VUELTA" -> "18"
            """
            codigo_upper = str(codigo).upper().strip()
            # Remover palabras comunes que indican sentido
            for sufijo in [' IDA', ' VUELTA', ' RETORNO', ' A', ' B', '-IDA', '-VUELTA']:
                if codigo_upper.endswith(sufijo):
                    codigo_upper = codigo_upper[:-len(sufijo)].strip()
            return codigo_upper

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
                    linea_codigo_base=extraer_codigo_base(lr.linea.codigo),
                    linea_nombre=lr.linea.nombre,
                    linea_ruta_id=lr.id,
                    numero_ruta=lr.numero_ruta,
                    es_caminar=False,
                )
                edges[prev_lp.punto_id].append(edge)

        total_edges_bus = sum(len(v) for v in edges.values())
        print(f"[GRAFO] Aristas de BUS totales: {total_edges_bus}")

        # Crear un diccionario para mapear linea_ruta_id a linea_codigo_base
        linea_ruta_to_codigo_base = {}
        for edge_list in edges.values():
            for edge in edge_list:
                if not edge.es_caminar and edge.linea_ruta_id is not None:
                    linea_ruta_to_codigo_base[edge.linea_ruta_id] = edge.linea_codigo_base
        print(f"[GRAFO] Mapeo linea_ruta_id -> codigo_base: {len(linea_ruta_to_codigo_base)} entradas")

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
            # Distancia real por calles (no línea recta)
            dist_km_real = dist_km * WALK_DISTANCE_FACTOR

            t_min = tiempo_desde_dist_km(dist_km)
            edge = Edge(
                from_punto_id=ORIGIN_NODE_ID,
                to_punto_id=p.id,
                tiempo=t_min,
                distancia=Decimal(str(dist_km_real)),  # guardar distancia real
                linea_id=None,
                linea_codigo="CAMINAR_ORIGEN",
                linea_codigo_base="CAMINAR",
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
                dist_km_real = dist_km * WALK_DISTANCE_FACTOR  # distancia por calles
                t_min = tiempo_desde_dist_km(dist_km)
                dist_dec = Decimal(str(dist_km_real))  # guardar distancia real

                e_ij = Edge(
                    from_punto_id=p_i.id,
                    to_punto_id=p_j.id,
                    tiempo=t_min,
                    distancia=dist_dec,
                    linea_id=None,
                    linea_codigo="CAMINAR",
                    linea_codigo_base="CAMINAR",
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
                    linea_codigo_base="CAMINAR",
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
            dist_km_real = dist_km * WALK_DISTANCE_FACTOR  # distancia por calles
            t_min = tiempo_desde_dist_km(dist_km)
            edge = Edge(
                from_punto_id=p.id,
                to_punto_id=DEST_NODE_ID,
                tiempo=t_min,
                distancia=Decimal(str(dist_km_real)),  # guardar distancia real
                linea_id=None,
                linea_codigo="CAMINAR_DESTINO",
                linea_codigo_base="CAMINAR",
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
        K_RUTAS = 3  # Número de rutas alternativas a buscar

        dist = {}
        prev_state = {}
        pq = []

        start_state = (ORIGIN_NODE_ID, None, 0) 
        dist[start_state] = Decimal("0")
        heapq.heappush(pq, (Decimal("0"), ORIGIN_NODE_ID, None, 0))
        print(f"[DIJKSTRA] Estado inicial: punto={ORIGIN_NODE_ID}, linea_ruta=None, trasbordos=0")

        dest_states = []  # Lista de estados que llegaron al destino
        debug_steps = 0
        max_debug_steps = 300 

        while pq and len(dest_states) < K_RUTAS:
            cost, punto_id, linea_ruta_id, trasbordos = heapq.heappop(pq)
            state = (punto_id, linea_ruta_id, trasbordos)

            if debug_steps < max_debug_steps:
                print(
                    f"[DIJKSTRA] POP cost={cost} punto={punto_id} "
                    f"linea_ruta={linea_ruta_id} trasbordos={trasbordos}"
                )

            if dist.get(state, None) is not None and cost > dist[state]:
                if debug_steps < max_debug_steps:
                    print("   [DIJKSTRA] Estado desactualizado, se omite.")
                continue

            if punto_id == DEST_NODE_ID:
                dest_states.append((state, cost))
                print(
                    f"[DIJKSTRA] >>> Alcanzado nodo DESTINO #{len(dest_states)} "
                    f"(id={DEST_NODE_ID}) con costo={cost} trasbordos={trasbordos}"
                )
                if len(dest_states) >= K_RUTAS:
                    break
                continue  # Continuar buscando más rutas

            edges_punto = edges.get(punto_id, [])
            for edge in edges_punto:
                if edge.es_caminar:
                    # Si caminamos, mantenemos la linea_ruta actual
                    edge_linea_ruta = linea_ruta_id
                    edge_linea_codigo_base = None
                else:
                    # Si tomamos un bus, la nueva linea_ruta es la del edge
                    edge_linea_ruta = edge.linea_ruta_id
                    edge_linea_codigo_base = edge.linea_codigo_base

                nuevo_trasbordos = trasbordos
                nueva_linea_ruta = linea_ruta_id
                transfer_penalty = Decimal("0")

                # Obtener el codigo_base de la linea actual (si existe)
                linea_codigo_base_actual = None
                if linea_ruta_id is not None:
                    linea_codigo_base_actual = linea_ruta_to_codigo_base.get(linea_ruta_id)

                if edge_linea_ruta is not None:
                    if nueva_linea_ruta is None:
                        # Primera vez subiendo a una línea
                        nueva_linea_ruta = edge_linea_ruta
                    elif nueva_linea_ruta != edge_linea_ruta:
                        # Cambio de linea_ruta_id
                        # Verificar si es transbordo real (diferente codigo_base) o solo cambio de sentido
                        if linea_codigo_base_actual and edge_linea_codigo_base and linea_codigo_base_actual != edge_linea_codigo_base:
                            # Transbordo real a línea diferente
                            nuevo_trasbordos = trasbordos + 1
                            transfer_penalty = TRANSFER_PENALTY_MIN
                            if debug_steps < max_debug_steps:
                                print(
                                    f"   [DIJKSTRA] TRANSBORDO detectado: "
                                    f"linea_anterior={linea_codigo_base_actual} -> linea_nueva={edge_linea_codigo_base} "
                                    f"({edge.linea_codigo})"
                                )
                        else:
                            # Cambio de sentido en la misma línea - aplicar penalización de transbordo
                            # pero sin aumentar el contador de trasbordos
                            transfer_penalty = TRANSFER_PENALTY_MIN
                            if debug_steps < max_debug_steps:
                                print(
                                    f"   [DIJKSTRA] CAMBIO DE SENTIDO detectado: "
                                    f"linea_ruta_anterior={linea_ruta_id} -> linea_ruta_nueva={edge_linea_ruta} "
                                    f"(mismo codigo_base={edge_linea_codigo_base})"
                                )
                        nueva_linea_ruta = edge_linea_ruta


                if nuevo_trasbordos > max_trasbordos:
                    if debug_steps < max_debug_steps:
                        print(
                            f"   [DIJKSTRA] Salto a {edge.to_punto_id} por "
                            f"linea={edge.linea_codigo} descartado: "
                            f"trasbordos {nuevo_trasbordos} > {max_trasbordos}"
                        )
                    continue

                nuevo_cost = cost + edge.tiempo + transfer_penalty
                next_state = (edge.to_punto_id, nueva_linea_ruta, nuevo_trasbordos)

                if nuevo_cost < dist.get(next_state, INF):
                    dist[next_state] = nuevo_cost
                    prev_state[next_state] = (state, edge)
                    heapq.heappush(
                        pq,
                        (nuevo_cost, edge.to_punto_id, nueva_linea_ruta, nuevo_trasbordos),
                    )
                    if debug_steps < max_debug_steps:
                        print(
                            f"   [DIJKSTRA] PUSH -> punto={edge.to_punto_id} "
                            f"linea_ruta={nueva_linea_ruta} ({edge.linea_codigo}) "
                            f"trasbordos={nuevo_trasbordos} "
                            f"cost={nuevo_cost} (penalizacion_trasbordo={transfer_penalty})"
                        )

            debug_steps += 1

        print(f"[DIJKSTRA] Estados totales visitados: {len(dist)}")
        print(f"[DIJKSTRA] Rutas al destino encontradas: {len(dest_states)}")

        if not dest_states:
            print("[DIJKSTRA] *** NO SE ENCONTRÓ RUTA AL DESTINO ***")
            return Response(
                {
                    "detail": "No se encontró una ruta entre los puntos con un máximo de 2 trasbordos."
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        # Procesar cada ruta encontrada
        rutas_procesadas = []
        
        for idx_ruta, (best_state, cost_final) in enumerate(dest_states):
            print(f"\n[RUTA #{idx_ruta + 1}] Procesando estado: {best_state}, costo={cost_final}")

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

            print(f"[RUTA #{idx_ruta + 1}] Segmentos: {len(segmentos)}")

            node_ids = [st[0] for st in path_states]
            num_nodos = len(node_ids)

            linea_trasbordo_flags = [None] * num_nodos

            for i in range(1, num_nodos - 1):
                prev_line_ruta = path_states[i - 1][1]
                curr_line_ruta = path_states[i][1]

                if prev_line_ruta is None:
                    continue

                if prev_line_ruta != curr_line_ruta and curr_line_ruta is not None:
                    incoming_edge = segmentos[i - 1]
                    if not incoming_edge.es_caminar:
                        # Verificar si es transbordo real (diferente codigo_base)
                        prev_codigo_base = linea_ruta_to_codigo_base.get(prev_line_ruta)
                        curr_codigo_base = incoming_edge.linea_codigo_base
                        
                        # Solo marcar como transbordo si cambia el codigo_base
                        if prev_codigo_base and curr_codigo_base and prev_codigo_base != curr_codigo_base:
                            linea_trasbordo_flags[i] = incoming_edge.linea_codigo

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
                        "linea_id": edge.linea_id,
                    }
            lineas_usadas = list(lineas_usadas_map.values())

            # Agrupar por linea_id para mostrar solo una vez cada línea (independiente del sentido)
            lineas_unicas = {}
            for linea in lineas_usadas:
                linea_id = linea["linea_id"]
                if linea_id not in lineas_unicas:
                    lineas_unicas[linea_id] = linea

            if lineas_unicas:
                detalles = [
                    f"{l['codigo']}"
                    for l in lineas_unicas.values()
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

            print(f"[RUTA #{idx_ruta + 1}] Descripción: {descripcion_ruta}")

            ruta_data = {
                "lineas": lineas_usadas,
                "descripcion_ruta": descripcion_ruta,
                "distancia_total": distancia_acumulada,
                "tiempo_total": tiempo_acumulado,
                "puntos": puntos_data,
            }
            rutas_procesadas.append(ruta_data)

        payload = {"rutas": rutas_procesadas}

        resp_serializer = MultipleRutasSerializer(instance=payload)
        return Response(resp_serializer.data, status=status.HTTP_200_OK)
