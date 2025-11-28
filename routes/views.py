from decimal import Decimal

from django.shortcuts import get_object_or_404

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

from drf_spectacular.utils import extend_schema, OpenApiParameter

from .models import Linea, LineaRuta
from .serializers import LineaSerializer, RutaConPuntosSerializer


class LineaListView(APIView):
    """
    Lista todas las líneas disponibles.
    """

    @extend_schema(
        operation_id='listar_lineas',
        summary='Listar todas las líneas',
        description='Devuelve la lista completa de líneas de microbús.',
        responses={200: LineaSerializer(many=True)},
    )
    def get(self, request):
        lineas = Linea.objects.all().order_by('codigo')
        serializer = LineaSerializer(lineas, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

class LineaRutaPuntosView(APIView):
    """
    Devuelve los puntos de una ruta (ida/vuelta) de una línea,
    con tiempo y distancia acumulados.
    """

    @extend_schema(
        operation_id='obtener_puntos_ruta_linea',
        summary='Puntos de una línea por sentido (ida/vuelta)',
        description=(
            "Dado el código de una línea (ej. L001) y el sentido de la ruta "
            "(1 = ida, 2 = vuelta), devuelve todos los puntos ordenados con:\n"
            "- tiempo del tramo\n"
            "- tiempo acumulado hasta cada punto (tiempo de llegada)\n"
            "- distancia del tramo\n"
            "- distancia acumulada"
        ),
        parameters=[
            OpenApiParameter(
                name='codigo',
                description='Código de la línea (por ejemplo L001)',
                required=True,
                type=str,
                location=OpenApiParameter.PATH,
            ),
            OpenApiParameter(
                name='sentido',
                description='Sentido de la ruta: 1 = ida, 2 = vuelta',
                required=True,
                type=int,
                location=OpenApiParameter.PATH,
            ),
        ],
        responses={200: RutaConPuntosSerializer},
    )
    def get(self, request, codigo: str, sentido: int):
        if sentido not in (1, 2):
            return Response(
                {'detail': 'El sentido debe ser 1 (ida) o 2 (vuelta).'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        linea = get_object_or_404(Linea, codigo=codigo)

        linea_ruta = get_object_or_404(
            LineaRuta,
            linea=linea,
            numero_ruta=sentido,
        )

        puntos_qs = (
            linea_ruta.puntos  # related_name en LineaPunto
            .select_related('punto')
            .order_by('orden')
        )

        puntos_data = []
        tiempo_acumulado = Decimal('0')
        distancia_acumulada = Decimal('0')

        for lp in puntos_qs:
            tiempo_acumulado += lp.tiempo
            distancia_acumulada += lp.distancia

            puntos_data.append({
                'orden': lp.orden,
                'latitud': lp.latitud,
                'longitud': lp.longitud,
                'descripcion': lp.punto.descripcion,
                'distancia_tramo': lp.distancia,
                'distancia_acumulada': distancia_acumulada,
                'tiempo_tramo': lp.tiempo,
                'tiempo_acumulado': tiempo_acumulado,
            })

        payload = {
            'linea_codigo': linea.codigo,
            'linea_nombre': linea.nombre,
            'sentido': sentido,
            'descripcion_ruta': linea_ruta.descripcion,
            'distancia_total': linea_ruta.distancia,
            'tiempo_total': linea_ruta.tiempo,
            'puntos': puntos_data,
        }

        serializer = RutaConPuntosSerializer(instance=payload)
        return Response(serializer.data, status=status.HTTP_200_OK)