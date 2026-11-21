# MainchPrintAgent — Puente Local de Impresión (Windows)

Agente liviano que corre en el PC de caja y permite que el POS imprima la comanda
**automáticamente, sin diálogo del navegador**, enviando los bytes ESC/POS directo
al spooler de Windows.

- Escucha solo en `http://127.0.0.1:9100` (nunca expuesto a internet).
- Requiere Python 3 y `pywin32` (el instalador los prepara).
- No abre ventanas: se inicia con Windows mediante un lanzador `.vbs`.

## Instalación en 3 pasos

1. Copia la carpeta `print_agent` al PC de caja (USB o descarga).
2. Haz doble clic en **`instalar_windows.bat`** y espera el mensaje
   *"INSTALACION COMPLETADA"*. Instala `pywin32`, copia el agente y lo deja
   arrancando automáticamente con Windows.
3. Abre el POS en Chrome/Edge → **Configuración → Impresora** → pulsa
   **"Detectar Impresoras"**, elige tu impresora térmica y luego
   **"Imprimir Ticket de Prueba"**.

Desde ese momento el POS muestra el badge **"Conectado"** en la tarjeta
*Puente Local Windows* e imprime las comandas sin ninguna ventana emergente.

## Verificación rápida

Abre en el navegador: <http://127.0.0.1:9100/health>

Deberías ver algo como:

```json
{"status": "ok", "version": "1.0.0", "platform": "win32", "default_printer": "EPSON TM-T20III", "dry_run": false}
```

Listar impresoras: <http://127.0.0.1:9100/printers>

## Uso manual (opcional)

```bat
python "%LOCALAPPDATA%\MainchPrintAgent\mainch_print_agent.py"
```

Variables de entorno:

| Variable | Efecto |
| --- | --- |
| `MAINCH_PRINT_PORT` | Puerto de escucha (default `9100`). |
| `MAINCH_PRINT_HOST` | Interfaz de escucha (default `127.0.0.1`). |
| `MAINCH_AGENT_DRY_RUN=1` | Simula la impresión sin tocar el spooler (diagnóstico). |
| `MAINCH_AGENT_VERBOSE=1` | Muestra cada petición en la consola. |

## Desinstalar

Doble clic en **`desinstalar_windows.bat`**: detiene el agente, elimina el
autoarranque y borra `%LOCALAPPDATA%\MainchPrintAgent`.

## Problemas frecuentes

- **Badge "No detectado"**: el agente no está corriendo; ejecuta de nuevo
  `instalar_windows.bat` y revisa <http://127.0.0.1:9100/health>.
- **"pywin32 no esta instalado"**: abre CMD y ejecuta
  `python -m pip install --upgrade pywin32`.
- **Imprime en la impresora equivocada**: selecciona la impresora correcta en el
  desplegable del POS (queda guardada en este equipo).
- **No imprime nada**: verifica en *Configuración de Windows → Impresoras* que la
  térmica esté instalada, encendida y con papel.
