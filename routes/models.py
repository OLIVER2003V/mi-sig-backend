from django.db import models


class Linea(models.Model):
    codigo = models.CharField(max_length=10, unique=True)
    nombre = models.CharField(max_length=100, blank=True)
    color = models.CharField(max_length=7)
    imagen_microbus = models.ImageField(
        upload_to='microbuses/',
        blank=True,
        null=True
    )
    fecha_creacion = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.codigo} - {self.nombre or 'Sin nombre'}"
    

class Punto(models.Model):
    latitud = models.DecimalField(max_digits=9, decimal_places=6)
    longitud = models.DecimalField(max_digits=9, decimal_places=6)
    descripcion = models.CharField(max_length=255, blank=True)

    def __str__(self):
        return self.descripcion or f"{self.latitud}, {self.longitud}"


class LineaRuta(models.Model):
    linea = models.ForeignKey(
        Linea,
        on_delete=models.CASCADE,
        related_name='rutas'
    )
    numero_ruta = models.PositiveIntegerField()
    descripcion = models.CharField(max_length=255)
    distancia = models.DecimalField(max_digits=10, decimal_places=2)
    tiempo = models.DecimalField(max_digits=6, decimal_places=2)

    class Meta:
        unique_together = ('linea', 'numero_ruta')

    def __str__(self):
        return f"{self.linea.codigo} - Ruta {self.numero_ruta}: {self.descripcion}"


class LineaPunto(models.Model):
    linea_ruta = models.ForeignKey(
        LineaRuta,
        on_delete=models.CASCADE,
        related_name='puntos'
    )
    punto = models.ForeignKey(
        Punto,
        on_delete=models.CASCADE,
        related_name='lineas_ruta'
    )
    orden = models.PositiveIntegerField()

    latitud = models.DecimalField(max_digits=9, decimal_places=6)
    longitud = models.DecimalField(max_digits=9, decimal_places=6)

    distancia = models.DecimalField(max_digits=10, decimal_places=2)
    tiempo = models.DecimalField(max_digits=6, decimal_places=2)

    class Meta:
        ordering = ['linea_ruta', 'orden']
        unique_together = ('linea_ruta', 'orden')

    def __str__(self):
        return f"{self.linea_ruta} - Punto {self.orden}"
