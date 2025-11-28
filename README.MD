# Guia para arrancar el servidor:

## Iniciar un virtual environment:

```bash
python -m venv venv

venv/Scripts\Activate.ps1
```

## Instalar dependencias:

```bash
pip install -r requirements.txt
```

## Aplicar Migraciones:


```bash
python manage.py makemigration

python manage.py migrate
```

## Poblar la bd con los datos propuestos por el ing:

```bash
python seed.py
```
## Arrancar el servidor:

### Para web
```bash
python manage.py runserver 0.0.0.0:8000
```

### Para emulador
```bash
python manage.py runserver
```