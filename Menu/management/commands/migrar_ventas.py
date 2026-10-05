"""
Management command: migrar_ventas
Migra las ventas históricas de la base antigua (MainchApp legacy SQLite) a la
base multi-tenant actual: Orden y OrdenItem, mapeando los platos legacy a los
nuevos platos/combos del catálogo migrado.

- Preserva monto_total, descuento, fecha, hora, estado, canal y tipo de pago.
- Reconstruye precio_unitario desde el valor del plato legacy.
- Los turnos legacy (franjas horarias) NO se mapean: se marca emitida_por_admin=True.
- Platos legacy sin equivalente (bebidas, salsas, etc.) se crean como nuevos platos.
"""
import os
import sqlite3
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from Menu.models import Categoria, Menu, Orden, OrdenItem, Plato, Restaurante

OLD_DB_DEFAULT = (
    "/Users/chuanzhiqiu/Desktop/untitled folder/mbd/Proyecto final/"
    "MainchApp/MainchApp/_internal/db.sqlite3"
)

# Mapeo old_plato_id -> ('plato'|'menu'|'nuevo', destino)
#   'plato'  -> nombre del nuevo Plato existente
#   'menu'   -> nombre del nuevo Menú (combo) existente
#   'nuevo'  -> (nombre, categoria, precio) para crear un Plato nuevo
MAPPING = {
    53: ("nuevo", ("Salsa", "Extras", "200")),
    54: ("plato", "Arrollado Primavera (3 unidades)"),
    55: ("plato", "Arrollado Jamón Queso (3 unidades)"),
    56: ("plato", "Arrollado Primavera (6 unidades)"),
    57: ("plato", "Wantán Frito (8 unidades)"),
    58: ("plato", "Arrollado Jamón Queso (6 unidades)"),
    59: ("plato", "Wantán Frito (4 unidades)"),
    60: ("plato", "Colación Pollo con Diente de Dragón"),
    61: ("plato", "Colación Pollo Piña"),
    62: ("plato", "Colación Carne Mongoliana"),
    63: ("plato", "Colación Carne Mongoliana"),
    64: ("plato", "Colación Carne Mongoliana (solo cerdo)"),
    65: ("plato", "Colación Carne Mongoliana"),
    66: ("plato", "Colación Chapsui Especial"),
    67: ("plato", "Colación Chaumín Especial"),
    68: ("plato", "Colación Vegetariana"),
    69: ("plato", "Colación Vegetariana"),
    70: ("plato", "Colación Chaumín Mongoliano"),
    71: ("plato", "Colación Chaumín Mongoliano"),
    72: ("plato", "Colación Chaumín Mongoliano (solo cerdo)"),
    73: ("plato", "Colación Chaumín Mongoliano"),
    74: ("plato", "Colación Pollo Keitén"),
    75: ("plato", "Colación Cerdo Tausi"),
    76: ("nuevo", ("Bebida Coca-Cola Lata", "Bebidas", "1200")),
    77: ("nuevo", ("Bebida Coca-Cola Zero Lata", "Bebidas", "1200")),
    78: ("nuevo", ("Bebida Sprite Lata", "Bebidas", "1200")),
    79: ("nuevo", ("Bebida Fanta Lata", "Bebidas", "1200")),
    83: ("nuevo", ("Bebida Vital", "Bebidas", "1000")),
    84: ("plato", "Arroz Blanco"),
    85: ("plato", "Arroz Chaufán"),
    86: ("plato", "Arroz Especial"),
    87: ("plato", "Chaumín Especial"),
    88: ("plato", "Chapsui de Verduras"),
    89: ("plato", "Chaumín de Verduras"),
    90: ("plato", "Chapsui de Pollo"),
    91: ("plato", "Pollo Tausi"),
    92: ("plato", "Pollo Piña"),
    93: ("plato", "Pollo Keitén"),
    94: ("plato", "Cerdo al Ajo"),
    95: ("plato", "Cerdo Tausi"),
    96: ("plato", "Carne Mongoliana"),
    97: ("plato", "Carne Tausi"),
    98: ("plato", "Chapsui de Carne"),
    99: ("plato", "Chapsui Especial"),
    100: ("plato", "Fuyón Especial"),
    101: ("plato", "Especial Mainch"),
    102: ("menu", "Menú para 2 personas"),
    103: ("menu", "Menú para 3 personas"),
    104: ("menu", "Menú para 4 personas"),
    105: ("menu", "Menú para 6 personas"),
    106: ("nuevo", ("Jugo 400cc", "Bebidas", "1500")),
    107: ("nuevo", ("Bebida Coca-Cola 1L", "Bebidas", "1800")),
    108: ("nuevo", ("Bebida Monster", "Bebidas", "2000")),
    110: ("plato", "Arrollado Primavera (6 unidades)"),
    111: ("plato", "Arrollado Jamón Queso (6 unidades)"),
    112: ("plato", "Wantán Frito (8 unidades)"),
    113: ("plato", "Colación Pollo con Diente de Dragón"),
    114: ("plato", "Colación Pollo Piña"),
    115: ("plato", "Colación Carne Mongoliana"),
    116: ("plato", "Colación Carne Mongoliana"),
    117: ("plato", "Colación Carne Mongoliana (solo cerdo)"),
    118: ("plato", "Colación Carne Mongoliana"),
    119: ("plato", "Colación Chapsui Especial"),
    120: ("plato", "Colación Vegetariana"),
    121: ("plato", "Colación Chaumín Mongoliano"),
    122: ("plato", "Arroz Blanco"),
    123: ("plato", "Arroz Chaufán"),
    124: ("plato", "Arroz Especial"),
    125: ("plato", "Chapsui de Verduras"),
    126: ("plato", "Chaumín de Verduras"),
    127: ("plato", "Chapsui de Pollo"),
    128: ("plato", "Pollo Keitén"),
    129: ("plato", "Pollo Piña"),
    130: ("plato", "Pollo Tausi"),
    131: ("plato", "Cerdo al Ajo"),
    132: ("plato", "Cerdo Tausi"),
    133: ("plato", "Carne Mongoliana"),
    134: ("plato", "Chapsui de Carne"),
    135: ("plato", "Carne Tausi"),
    136: ("plato", "Chaumín Especial"),
    137: ("plato", "Chapsui Especial"),
    138: ("plato", "Especial Mainch"),
    139: ("plato", "Fuyón Especial"),
    140: ("menu", "Menú para 2 personas"),
    141: ("menu", "Menú para 3 personas"),
    142: ("menu", "Menú para 4 personas"),
    143: ("menu", "Menú para 6 personas"),
    144: ("nuevo", ("Bebida Coca-Cola Lata", "Bebidas", "1200")),
    145: ("nuevo", ("Bebida Coca-Cola Zero Lata", "Bebidas", "1200")),
    146: ("nuevo", ("Bebida Fanta Lata", "Bebidas", "1200")),
    147: ("nuevo", ("Bebida Sprite Lata", "Bebidas", "1200")),
    149: ("plato", "Carne Mongoliana"),
    150: ("plato", "Carne Mongoliana"),
    151: ("plato", "Carne Mongoliana"),
    152: ("nuevo", ("Vaso Plástico", "Extras", "50")),
    153: ("plato", "Colación Pollo con Diente de Dragón"),
    154: ("plato", "Colación Pollo con Diente de Dragón"),
    155: ("nuevo", ("Menú Junaeb", "Extras", "2350")),
    157: ("plato", "Colación Carne Mongoliana + Papas Fritas"),
    158: ("plato", "Colación Carne Mongoliana + Papas Fritas"),
    159: ("plato", "Colación Carne Mongoliana + Papas Fritas"),
    160: ("plato", "Colación Cerdo Mongoliano + Papas Fritas"),
    161: ("plato", "Papa Sola"),
    162: ("nuevo", ("Extra", "Extras", "1500")),
    164: ("plato", "Colación Carne Mongoliana + Papas Fritas"),
    165: ("plato", "Colación Carne Mongoliana + Papas Fritas"),
    166: ("plato", "Colación Carne Mongoliana + Papas Fritas"),
    167: ("plato", "Colación Cerdo Mongoliano + Papas Fritas"),
    168: ("plato", "Bao zi (pollo, cerdo o vegetariano)"),
    169: ("menu", "Promo 2 Bao zi"),
    170: ("plato", "Consomé de Pollo"),
    171: ("menu", "Promo Consomé + Bao zi"),
    172: ("menu", "Promo Consomé + 2 Bao zi"),
    173: ("nuevo", ("Bebida Coca-Cola 1L", "Bebidas", "1800")),
    174: ("menu", "Promo 2 Bao zi"),
    175: ("plato", "Bao zi (pollo, cerdo o vegetariano)"),
    177: ("plato", "Colación Pollo con Diente de Dragón"),
}

BATCH = 1000


def _dec(value):
    if value is None:
        return Decimal("0.00")
    return Decimal(str(round(float(value), 2))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


class Command(BaseCommand):
    help = "Migra las ventas históricas (Orden/OrdenItem) desde la base legacy SQLite."

    def add_arguments(self, parser):
        parser.add_argument("--old-db", default=OLD_DB_DEFAULT, help="Ruta al db.sqlite3 legacy.")
        parser.add_argument("--tenant-slug", default="mainch", help="Slug del restaurante destino.")
        parser.add_argument(
            "--force",
            action="store_true",
            help="Permite re-ejecutar aunque ya existan órdenes (¡duplicará datos!).",
        )

    def handle(self, *args, **options):
        if not os.path.exists(options["old_db"]):
            raise CommandError(f"No se encontró la base legacy: {options['old_db']}")

        restaurante, _ = Restaurante.objects.get_or_create(
            slug=options["tenant_slug"],
            defaults={"nombre": "Mainch", "direccion": "Valparaíso, Chile"},
        )

        if Orden.objects.filter(restaurante=restaurante).exists() and not options["force"]:
            raise CommandError(
                "Ya existen órdenes para este tenant. Use --force solo si desea duplicar."
            )

        conn = sqlite3.connect(options["old_db"])
        conn.row_factory = sqlite3.Row

        # --- 1. Platos legacy (id -> valor) ---
        old_platos = {r["id"]: float(r["valor"]) for r in conn.execute("SELECT id, valor FROM Menu_plato")}

        # --- 2. Resolver mapeo a objetos nuevos ---
        plato_por_nombre = {p.nombre: p for p in Plato.objects.filter(restaurante=restaurante)}
        menu_por_nombre = {m.nombre: m for m in Menu.objects.filter(restaurante=restaurante)}
        categorias = {c.nombre: c for c in Categoria.objects.filter(restaurante=restaurante)}

        nuevos_creados = 0
        resolved = {}
        no_mapeados = []
        for old_id, (tipo, destino) in sorted(MAPPING.items()):
            obj = None
            if tipo == "plato":
                obj = plato_por_nombre.get(destino)
            elif tipo == "menu":
                obj = menu_por_nombre.get(destino)
            elif tipo == "nuevo":
                nombre, cat_nombre, precio = destino
                cat = categorias.get(cat_nombre)
                if cat is None:
                    cat, _ = Categoria.objects.get_or_create(
                        restaurante=restaurante, nombre=cat_nombre,
                        defaults={"orden": 99, "activo": True},
                    )
                    categorias[cat_nombre] = cat
                obj, created = Plato.objects.get_or_create(
                    restaurante=restaurante, nombre=nombre,
                    defaults={
                        "valor": Decimal(str(precio)),
                        "categoria": cat,
                        "descripcion": "Migrado desde base legacy.",
                    },
                )
                if created:
                    nuevos_creados += 1
            if obj is None:
                no_mapeados.append(old_id)
            else:
                resolved[old_id] = (tipo, obj)

        if no_mapeados:
            self.stdout.write(self.style.WARNING(
                f"Platos legacy sin mapear (sus ítems se omitirán): {no_mapeados}"
            ))
        self.stdout.write(f"Nuevos platos creados (bebidas/extras): {nuevos_creados}")

        # --- 3. Migrar órdenes ---
        total_ordenes = conn.execute("SELECT COUNT(*) c FROM Menu_orden").fetchone()["c"]
        orden_id_map = {}
        creadas = 0

        # bulk_create aplica pre_save() y por tanto auto_now_add; se desactiva
        # temporalmente para preservar las fechas/horas históricas del legacy.
        fecha_field = Orden._meta.get_field("fecha")
        hora_field = Orden._meta.get_field("hora")
        fecha_auto, hora_auto = fecha_field.auto_now_add, hora_field.auto_now_add
        fecha_field.auto_now_add = False
        hora_field.auto_now_add = False

        try:
            with transaction.atomic():
                cursor = conn.execute(
                    "SELECT id, cliente, estado, fecha, hora, monto_total, descuento, "
                    "canal_venta, tipo_pago FROM Menu_orden ORDER BY id"
                )
                while True:
                    rows = cursor.fetchmany(BATCH)
                    if not rows:
                        break
                    lote = []
                    old_ids = []
                    for r in rows:
                        dt = datetime.fromisoformat(f"{r['fecha']}T{r['hora']}")
                        estado = r["estado"]
                        tipo_pago = r["tipo_pago"]
                        if tipo_pago == "No especificado":
                            tipo_pago = None
                        lote.append(Orden(
                            restaurante=restaurante,
                            cliente=r["cliente"],
                            fecha=dt.date(),
                            hora=dt.time(),
                            estado=estado,
                            tipo_pago=tipo_pago,
                            canal_venta=r["canal_venta"],
                            tipo_servicio="delivery" if r["canal_venta"] == "Delivery" else "mostrador",
                            monto_total=_dec(r["monto_total"]),
                            descuento=_dec(r["descuento"]),
                            stock_descontado=(estado == Orden.ESTADO_COMPLETADA),
                            fecha_completada=(
                                timezone.make_aware(dt) if estado == Orden.ESTADO_COMPLETADA else None
                            ),
                            emitida_por_admin=True,
                        ))
                        old_ids.append(r["id"])
                    Orden.objects.bulk_create(lote)
                    for old_id, orden in zip(old_ids, lote):
                        orden_id_map[old_id] = orden.pk
                    creadas += len(rows)
                    if creadas % 10000 == 0:
                        self.stdout.write(f"  órdenes: {creadas}/{total_ordenes}")
        finally:
            fecha_field.auto_now_add = fecha_auto
            hora_field.auto_now_add = hora_auto
        self.stdout.write(self.style.SUCCESS(f"Órdenes migradas: {creadas}"))

        # --- 4. Migrar ítems ---
        total_items = conn.execute("SELECT COUNT(*) c FROM Menu_ordenitem").fetchone()["c"]
        items_creados = 0
        items_omitidos = 0

        with transaction.atomic():
            cursor = conn.execute(
                "SELECT id, orden_id, plato_id, menu_id, cantidad FROM Menu_ordenitem ORDER BY id"
            )
            while True:
                rows = cursor.fetchmany(BATCH)
                if not rows:
                    break
                lote = []
                for r in rows:
                    old_plato_id = r["plato_id"]
                    old_orden_id = r["orden_id"]
                    if old_plato_id not in resolved or old_orden_id not in orden_id_map:
                        items_omitidos += 1
                        continue
                    tipo, obj = resolved[old_plato_id]
                    precio = old_platos.get(old_plato_id, 0)
                    item = OrdenItem(
                        restaurante=restaurante,
                        orden_id=orden_id_map[old_orden_id],
                        plato=obj if tipo == "plato" else None,
                        menu=obj if tipo == "menu" else None,
                        cantidad=r["cantidad"] or 1,
                        precio_unitario=_dec(precio),
                    )
                    lote.append(item)
                OrdenItem.objects.bulk_create(lote)
                items_creados += len(lote)
                if items_creados % 20000 == 0:
                    self.stdout.write(f"  ítems: {items_creados}/{total_items}")
        self.stdout.write(self.style.SUCCESS(
            f"Ítems migrados: {items_creados} (omitidos: {items_omitidos})"
        ))

        conn.close()
        self.stdout.write(self.style.SUCCESS("Migración de ventas completada."))
