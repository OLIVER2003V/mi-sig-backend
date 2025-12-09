import os
from pathlib import Path
from decimal import Decimal

import django
import pandas as pd

# Configurar Django ANTES de importar modelos
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "buses.settings")
BASE_DIR = Path(__file__).resolve().parent

django.setup()

# Ahora sí podemos importar los modelos
from django.db import transaction
from routes.models import Linea, Punto, LineaRuta, LineaPunto



def clean_str(value):
    """Quita NaN y espacios."""
    if pd.isna(value):
        return ""
    return str(value).strip()


def to_decimal(value, default=0):
    """Convierte un valor a Decimal manejando NaN."""
    if pd.isna(value) or value is None:
        return Decimal(str(default))
    return Decimal(str(value))


@transaction.atomic
def run():
    xlsx_path = BASE_DIR / "DatosLineas.xlsx"
    if not xlsx_path.exists():
        raise FileNotFoundError(f"No se encontró el archivo {xlsx_path}")

    print(f"Usando archivo: {xlsx_path}")

    # Leer todas las hojas
    xls = pd.ExcelFile(xlsx_path)
    df_lineas = xls.parse("Lineas")
    df_puntos = xls.parse("Puntos")
    df_linea_ruta = xls.parse("LineaRuta")
    df_lineas_puntos = xls.parse("LineasPuntos")

    linea_map = {}        # IdLinea (Excel) -> Linea
    punto_map = {}        # IdPunto (Excel) -> Punto
    linea_ruta_map = {}   # IdLineaRuta (Excel) -> LineaRuta

    print("\nImportando Lineas...")
    for _, row in df_lineas.iterrows():
        id_excel = int(row["IdLinea"])
        codigo = clean_str(row["NombreLinea"]) 
        color = clean_str(row["ColorLinea"]) or "#000000"
        imagen_nombre = clean_str(row["ImagenMicrobus"])
        fecha_creacion = row["FechaCreacion"] if "FechaCreacion" in df_lineas.columns else None

        linea, created = Linea.objects.get_or_create(
            codigo=codigo,
            defaults={
                "nombre": codigo,
                "color": color,
            },
        )

        if imagen_nombre:
            linea.imagen_microbus.name = f"microbuses/{imagen_nombre}"
        if fecha_creacion is not None and not pd.isna(fecha_creacion):
            linea.fecha_creacion = fecha_creacion

        linea.save()
        linea_map[id_excel] = linea

        print(f"  {'CREADA' if created else 'USADA'} Linea {codigo} (IdExcel={id_excel})")

    print("\nImportando Puntos...")
    for _, row in df_puntos.iterrows():
        id_excel = int(row["IdPunto"])
        lat = to_decimal(row["Latitud"])
        lon = to_decimal(row["Longitud"])
        desc = clean_str(row["Descripcion"])

        punto, created = Punto.objects.get_or_create(
            latitud=lat,
            longitud=lon,
            defaults={"descripcion": desc},
        )

        if not created and desc and not punto.descripcion:
            punto.descripcion = desc
            punto.save()

        punto_map[id_excel] = punto
        print(f"  {'CREADO' if created else 'USADO'} Punto {id_excel} ({lat}, {lon})")

    print("\nImportando LineaRuta...")
    for _, row in df_linea_ruta.iterrows():
        id_excel = int(row["IdLineaRuta"])
        id_linea_excel = int(row["IdLinea"])
        id_ruta = int(row["IdRuta"])  # 1 = salida, 2 = retorno, etc.
        descripcion = clean_str(row["Descripcion"])
        distancia = to_decimal(row["Distancia"])
        tiempo = to_decimal(row["Tiempo"])

        linea = linea_map.get(id_linea_excel)
        if not linea:
            raise ValueError(f"No se encontró Linea con IdLinea={id_linea_excel} del Excel")

        linea_ruta, created = LineaRuta.objects.get_or_create(
            linea=linea,
            numero_ruta=id_ruta,
            defaults={
                "descripcion": descripcion,
                "distancia": distancia,
                "tiempo": tiempo,
            },
        )

        if not created:
            linea_ruta.descripcion = descripcion
            linea_ruta.distancia = distancia
            linea_ruta.tiempo = tiempo
            linea_ruta.save()

        linea_ruta_map[id_excel] = linea_ruta
        print(
            f"  {'CREADA' if created else 'USADA'} LineaRuta {linea.codigo} "
            f"Ruta {id_ruta} (IdExcel={id_excel})"
        )

    print("\nImportando LineasPuntos...")
    for _, row in df_lineas_puntos.iterrows():
        id_excel = int(row["IdLineaPunto"])
        id_linea_ruta_excel = int(row["IdLineaRuta"])
        id_punto_excel = int(row["IdPunto"])
        orden = int(row["Orden"])
        lat = to_decimal(row["Latitud"])
        lon = to_decimal(row["Longitud"])
        distancia = to_decimal(row["Distancia"])
        tiempo = to_decimal(row["Tiempo"])

        linea_ruta = linea_ruta_map.get(id_linea_ruta_excel)
        if not linea_ruta:
            raise ValueError(
                f"No se encontró LineaRuta con IdLineaRuta={id_linea_ruta_excel} del Excel"
            )

        punto = punto_map.get(id_punto_excel)
        if not punto:
            punto, _ = Punto.objects.get_or_create(
                latitud=lat,
                longitud=lon,
                defaults={"descripcion": ""},
            )

        linea_punto, created = LineaPunto.objects.get_or_create(
            linea_ruta=linea_ruta,
            orden=orden,
            defaults={
                "punto": punto,
                "latitud": lat,
                "longitud": lon,
                "distancia": distancia,
                "tiempo": tiempo,
            },
        )

        if not created:
            linea_punto.punto = punto
            linea_punto.latitud = lat
            linea_punto.longitud = lon
            linea_punto.distancia = distancia
            linea_punto.tiempo = tiempo
            linea_punto.save()

        print(
            f"  {'CREADA' if created else 'USADA'} LineaPunto (IdExcel={id_excel}) "
            f"LineaRutaExcel={id_linea_ruta_excel} PuntoExcel={id_punto_excel}"
        )

    print("\n✅     Importación terminada correctamente.")


if __name__ == "__main__":
    run()
