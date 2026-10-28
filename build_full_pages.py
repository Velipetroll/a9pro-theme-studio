import re
import a9pro_studio

# Obtener el HTML base completo de a9pro_studio.py
orig_html = a9pro_studio.get_studio_ui_html()

# Reemplazar rutas de assets absolutas por relativas
orig_html = orig_html.replace('/web_assets/', 'assets/')

# Separar el HTML en markup y script (buscar el último script del body)
script_start = orig_html.rfind('<script>')
html_head_and_body = orig_html[:script_start]

# En el head, inyectar los scripts necesarios y las fuentes @font-face
head_extra = """  <!-- Motores Client-Side para GitHub Pages (100% Funcional sin servidor) -->
  <script src="omggif.min.js"></script>
  <script src="pa_decoder.js"></script>
  <script src="pa_encoder.js"></script>
  <script src="themes_catalog.js"></script>
  <style>
    @font-face { font-family: 'Bebas Neue'; src: url('custom_fonts/BebasNeue-Regular.ttf'); }
    @font-face { font-family: 'Orbitron'; src: url('custom_fonts/Orbitron-Bold.ttf'); }
    @font-face { font-family: 'Press Start 2P'; src: url('custom_fonts/PressStart2P-Regular.ttf'); }
    @font-face { font-family: 'Pricedown'; src: url("custom_fonts/Pricedown Bl.otf"); }
    @font-face { font-family: 'Share Tech Mono'; src: url('custom_fonts/ShareTechMono-Regular.ttf'); }
  </style>
</head>"""

html_head_and_body = html_head_and_body.replace("</head>", head_extra, 1)

# Ahora construimos el JavaScript completo con soporte nativo de Web Bluetooth,
# procesamiento de GIF en navegador (omggif), strips en canvas, compilador .bin (pa_encoder) y fallback local.

with open("standalone_web_script.js", "r", encoding="utf-8") as f:
    client_script = f.read()

full_html = html_head_and_body + "  <script>\n" + client_script + "\n  </script>\n</body>\n</html>"

with open("standalone_web/index.html", "w", encoding="utf-8") as f:
    f.write(full_html)

with open("docs/index.html", "w", encoding="utf-8") as f:
    f.write(full_html)

print("¡standalone_web/index.html y docs/index.html generados exitosamente!")
