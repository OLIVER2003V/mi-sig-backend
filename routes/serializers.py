from rest_framework import serializers

from routes.models import Linea


class PuntoRutaSerializer(serializers.Serializer):
    orden = serializers.IntegerField()
    latitud = serializers.DecimalField(max_digits=9, decimal_places=6)
    longitud = serializers.DecimalField(max_digits=9, decimal_places=6)
    descripcion = serializers.CharField(allow_blank=True)

    distancia_tramo = serializers.DecimalField(max_digits=10, decimal_places=2)
    distancia_acumulada = serializers.DecimalField(max_digits=10, decimal_places=2)

    tiempo_tramo = serializers.DecimalField(max_digits=6, decimal_places=2)
    tiempo_acumulado = serializers.DecimalField(max_digits=6, decimal_places=2)

class LineaSerializer(serializers.ModelSerializer):
    """
    Serializer simple para listar líneas.
    Devuelve: id, codigo, nombre y color (hex).
    """
    class Meta:
        model = Linea
        fields = ['id', 'codigo', 'nombre', 'color']

class RutaConPuntosSerializer(serializers.Serializer):
    linea_codigo = serializers.CharField()
    linea_nombre = serializers.CharField(allow_blank=True)
    sentido = serializers.IntegerField()
    descripcion_ruta = serializers.CharField()

    distancia_total = serializers.DecimalField(max_digits=10, decimal_places=2)
    tiempo_total = serializers.DecimalField(max_digits=6, decimal_places=2)

    puntos = PuntoRutaSerializer(many=True)


class CoordenadasSerializer(serializers.Serializer):
    lat = serializers.FloatField()
    lon = serializers.FloatField()


class RutaPlanRequestSerializer(serializers.Serializer):
    inicio = CoordenadasSerializer()
    fin = CoordenadasSerializer()


class PuntoRutaPlanificadaSerializer(PuntoRutaSerializer):
    linea_trasbordo = serializers.CharField(
        allow_null=True,
        allow_blank=True,
        help_text="Código de la línea a la que se hace trasbordo en este punto (o null si no hay trasbordo).",
    )


class LineaTrayectoSerializer(serializers.Serializer):
    codigo = serializers.CharField()
    nombre = serializers.CharField(allow_blank=True)
    sentido = serializers.IntegerField()


class RutaPlanificadaSerializer(serializers.Serializer):
    lineas = LineaTrayectoSerializer(many=True)
    descripcion_ruta = serializers.CharField()

    distancia_total = serializers.DecimalField(max_digits=10, decimal_places=2)
    tiempo_total = serializers.DecimalField(max_digits=6, decimal_places=2)

    puntos = PuntoRutaPlanificadaSerializer(many=True)


class MultipleRutasSerializer(serializers.Serializer):
    """Serializer para devolver múltiples rutas alternativas"""
    rutas = RutaPlanificadaSerializer(many=True)
