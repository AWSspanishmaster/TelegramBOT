# Telegram Bot para seguimiento de direcciones en Hyperliquid

Este bot de Telegram permite a los usuarios agregar direcciones Ethereum para seguir sus operaciones (fills) en la plataforma Hyperliquid. Los usuarios pueden agregar, listar, eliminar direcciones y consultar las posiciones recientes.

---

## Características

- Comandos disponibles:
  - `/start`: Mensaje de bienvenida.
  - `/add <address>`: Añade una dirección Ethereum (debe comenzar con `0x` y tener 42 caracteres).
  - `/list`: Lista las direcciones agregadas por el usuario.
  - `/remove <address>`: Elimina una dirección de la lista del usuario.
  - `/positions`: Muestra un menú para seleccionar una dirección y consultar sus operaciones recientes.

- Consulta de operaciones recientes con API pública de Hyperliquid.

---

## Despliegue en Render sin usar Worker

Render requiere que el servicio escuche en un puerto para evitar que se detenga por timeout. Para evitar cambiar el plan a Worker, el bot incluye un servidor HTTP básico que responde en la ruta `/` para mantener el proceso activo.

### Detalles técnicos

- El bot corre con `python-telegram-bot` y `aiohttp`.
- Usa `nest_asyncio` para compatibilidad en entornos asíncronos.
- El servidor HTTP básico está implementado con `aiohttp` y escucha en el puerto que Render asigna (variable de entorno `PORT`).
- El bot y el servidor HTTP corren simultáneamente en el mismo proceso usando `asyncio`.

---

## Variables de entorno

- `TOKEN`: Token del bot de Telegram.

---

## Ejecución local

1. Clona este repositorio.
2. Instala las dependencias:

```bash
pip install -r requirements.txt
