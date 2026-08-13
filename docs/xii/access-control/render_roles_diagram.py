from reportlab.lib import colors
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas


OUT = "output/pdf/care-roles-access-overview.pdf"
W, H = 1500, 920

roles = [
    ("admin / admin", "Superusuario", "#7f1d1d"),
    ("care-volunteer", "Volunteer", "#0f766e"),
    ("care-staff", "Staff", "#0f766e"),
    ("care-doctor", "Doctor", "#0f766e"),
    ("care-nurse", "Nurse", "#0f766e"),
    ("care-fac-admin", "Facility Admin", "#0f766e"),
]

domains = [
    ("Pacientes y\nformularios", ["Super", "Volunteer", "Staff", "Doctor", "Nurse", "Facility Admin"]),
    ("Encuentros y\ndatos clínicos", ["Super", "Staff", "Doctor", "Nurse", "Facility Admin"]),
    ("Horarios, citas\ny tokens", ["Super", "Staff", "Doctor", "Nurse", "Facility Admin"]),
    ("Institución,\nubicaciones y\ndispositivos", ["Super", "Volunteer", "Staff", "Doctor", "Nurse", "Facility Admin"]),
    ("Organizaciones\ny usuarios", ["Super", "Volunteer", "Staff", "Doctor", "Nurse", "Facility Admin"]),
    ("Cuentas, cargos,\nfacturas y pagos", ["Super", "Volunteer", "Staff", "Doctor", "Nurse", "Facility Admin"]),
    ("Medicamentos\ny laboratorio", ["Super", "Volunteer", "Staff", "Doctor", "Nurse", "Facility Admin"]),
    ("Inventario y\nsuministros", ["Super", "Volunteer", "Staff", "Doctor", "Nurse", "Facility Admin"]),
    ("Cuestionarios\ny plantillas", ["Super", "Volunteer", "Staff", "Doctor", "Nurse", "Facility Admin"]),
    ("Reportes", ["Super", "Volunteer", "Staff", "Doctor", "Nurse", "Facility Admin"]),
    ("Configuración y\nadministración", ["Super", "Facility Admin"]),
]

role_keys = ["Super", "Volunteer", "Staff", "Doctor", "Nurse", "Facility"]


def fit_text(c, text, x, y, max_width, size=13, leading=15, color=colors.white, bold=False):
    c.setFillColor(color)
    c.setFont("Helvetica-Bold" if bold else "Helvetica", size)
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if stringWidth(line, "Helvetica-Bold" if bold else "Helvetica", size) > max_width:
            size = max(8, size - 1)
            c.setFont("Helvetica-Bold" if bold else "Helvetica", size)
        c.drawCentredString(x, y - i * leading, line)


def arrow(c, x1, y1, x2, y2, color="#cbd5e1"):
    c.setStrokeColor(colors.HexColor(color))
    c.setLineWidth(1.2)
    c.line(x1, y1, x2, y2)
    c.setFillColor(colors.HexColor(color))
    c.setStrokeColor(colors.HexColor(color))
    c.setLineJoin(1)
    c.line(x2, y2, x2 - 7, y2 + 4)
    c.line(x2, y2, x2 - 7, y2 - 4)


def main():
    c = canvas.Canvas(OUT, pagesize=(W, H))
    c.setTitle("CARE - Mapa de accesos por perfil")
    c.setFillColor(colors.HexColor("#f8fafc"))
    c.rect(0, 0, W, H, fill=1, stroke=0)

    c.setFillColor(colors.HexColor("#111827"))
    c.setFont("Helvetica-Bold", 28)
    c.drawString(42, H - 50, "CARE - Mapa de accesos por perfil")
    c.setFillColor(colors.HexColor("#64748b"))
    c.setFont("Helvetica", 12)
    c.drawString(43, H - 72, "Resumen funcional. El acceso efectivo también depende del ámbito asignado.")

    role_x, role_w = 45, 185
    role_top = H - 120
    role_h, role_gap = 70, 12
    domain_x = 360
    domain_w = 220
    domain_h = 56
    cols = 4
    row_gap = 26
    domain_gap = 25

    role_centers = {}
    for i, (username, label, fill) in enumerate(roles):
        y = role_top - i * (role_h + role_gap) - role_h
        c.setFillColor(colors.HexColor(fill))
        c.roundRect(role_x, y, role_w, role_h, 12, fill=1, stroke=0)
        fit_text(c, username, role_x + role_w / 2, y + 43, role_w - 16, size=13, bold=True)
        fit_text(c, label, role_x + role_w / 2, y + 23, role_w - 16, size=12, color=colors.HexColor("#d1fae5"))
        role_centers["Super" if label == "Superusuario" else label] = (role_x + role_w, y + role_h / 2)

    domain_centers = {}
    for i, (label, access) in enumerate(domains):
        col = i % cols
        row = i // cols
        x = domain_x + col * (domain_w + domain_gap)
        y = role_top - row * (domain_h + row_gap) - domain_h
        c.setFillColor(colors.white)
        c.setStrokeColor(colors.HexColor("#cbd5e1"))
        c.setLineWidth(1.1)
        c.roundRect(x, y, domain_w, domain_h, 10, fill=1, stroke=1)
        fit_text(c, label, x + domain_w / 2, y + 34, domain_w - 18, size=12, leading=14, color=colors.HexColor("#1e293b"), bold=True)
        domain_centers[i] = (x, y + domain_h / 2)
        for key in access:
            sx, sy = role_centers[key]
            arrow(c, sx + 4, sy, x - 4, y + domain_h / 2, "#d7dee8")

    legend_y = 35
    c.setFillColor(colors.HexColor("#475569"))
    c.setFont("Helvetica", 10)
    c.drawString(45, legend_y, "Lectura: permiso declarado, sujeto a institución/organización/paciente/encuentro asociado.")
    c.drawRightString(W - 45, legend_y, "Línea base - 12 ago 2026")
    c.save()


if __name__ == "__main__":
    main()
