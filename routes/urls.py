from django.urls import path

from routes.dijkstra_views import PlanificarRutaView
from .views import LineaListView, LineaRutaPuntosView

urlpatterns = [
    path("lineas/", LineaListView.as_view(), name="linea-list"),
    path(
        "lineas/<str:codigo>/rutas/<int:sentido>/puntos/",
        LineaRutaPuntosView.as_view(),
        name="linea-ruta-puntos",
    ),
    path(
        "rutas/planificar/",
        PlanificarRutaView.as_view(),
        name="ruta-planificar",
    ),
]
