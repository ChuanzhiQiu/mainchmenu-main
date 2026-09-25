"""
Django Management Command: generar_datos_demo
Generates realistic seed data for MainchApp:
- Culinary raw materials (Insumo) with costs and safety stock
- Dishes (Plato) with recipes (RecetaItem)
- Combo menus (Menu)
- Realistic completed sales history (Orden, OrdenItem) for AI forecasting
Feature F26 — MBAn UAI 2026-B Track A.
"""

from decimal import Decimal
import datetime
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from Menu.models import Insumo, Plato, Menu, Orden, OrdenItem, RecetaItem, MovimientoStock


class Command(BaseCommand):
    help = "Genera datos sintéticos y realistas de insumos, platos, recetas y órdenes para pruebas y previsión IA."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dias",
            type=int,
            default=14,
            help="Días de historial de ventas a generar (por defecto: 14).",
        )
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Eliminar datos de demostración anteriores antes de sembrar.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        dias = max(1, options.get("dias", 14))
        do_reset = options.get("reset", False)

        if do_reset:
            self.stdout.write("Limpiando datos demo previos...")
            Orden.objects.filter(cliente__startswith="Demo").delete()

        self.stdout.write("Sembrando insumos de cocina (materias primas)...")
        # 1. Insumos
        insumos_data = [
            ("INS-CARNE", "Lomo Liso Vacuno", Insumo.UNIDAD_KG, Decimal("15.000"), Decimal("10.000"), Decimal("8500.000")),
            ("INS-POLLO", "Pechuga de Pollo", Insumo.UNIDAD_KG, Decimal("12.000"), Decimal("6.000"), Decimal("4200.000")),
            ("INS-CERDO", "Pulpa de Cerdo", Insumo.UNIDAD_KG, Decimal("8.000"), Decimal("5.000"), Decimal("4800.000")),
            ("INS-PAPA", "Papa Limpia Seleccionada", Insumo.UNIDAD_KG, Decimal("40.000"), Decimal("15.000"), Decimal("1000.000")),
            ("INS-TOMATE", "Tomate Larga Vida", Insumo.UNIDAD_KG, Decimal("12.000"), Decimal("8.000"), Decimal("1200.000")),
            ("INS-PALTA", "Palta Hass Primera", Insumo.UNIDAD_KG, Decimal("6.000"), Decimal("6.000"), Decimal("4500.000")),
            ("INS-CEBOLLA", "Cebolla Valdiviana", Insumo.UNIDAD_KG, Decimal("15.000"), Decimal("5.000"), Decimal("900.000")),
            ("INS-QUESO", "Queso Chanco Laminado", Insumo.UNIDAD_KG, Decimal("10.000"), Decimal("5.000"), Decimal("7200.000")),
            ("INS-PAN-FRICA", "Pan Frica Artesanal", Insumo.UNIDAD_UN, Decimal("60.000"), Decimal("20.000"), Decimal("350.000")),
            ("INS-PAN-MARRAQ", "Pan Marraqueta Crujiente", Insumo.UNIDAD_UN, Decimal("50.000"), Decimal("20.000"), Decimal("300.000")),
            ("INS-VIANESA", "Vianesa Sureña", Insumo.UNIDAD_UN, Decimal("40.000"), Decimal("15.000"), Decimal("400.000")),
            ("INS-HUEVO", "Huevo Extra Grande", Insumo.UNIDAD_UN, Decimal("60.000"), Decimal("24.000"), Decimal("220.000")),
            ("INS-ACEITE", "Aceite Maravilla", Insumo.UNIDAD_LT, Decimal("20.000"), Decimal("10.000"), Decimal("2200.000")),
            ("INS-MAYO", "Mayonesa Casera", Insumo.UNIDAD_LT, Decimal("8.000"), Decimal("4.000"), Decimal("3200.000")),
            ("INS-BEBIDA", "Bebida Lata 350cc", Insumo.UNIDAD_UN, Decimal("48.000"), Decimal("24.000"), Decimal("800.000")),
        ]

        insumo_objs = {}
        for codigo, nombre, unidad, stock, stock_min, costo in insumos_data:
            ins, _ = Insumo.objects.update_or_create(
                codigo=codigo,
                defaults={
                    "nombre": nombre,
                    "unidad_medida": unidad,
                    "stock_actual": stock,
                    "stock_minimo": stock_min,
                    "costo_unitario": costo,
                    "activo": True,
                },
            )
            insumo_objs[codigo] = ins

        self.stdout.write(f"  -> {len(insumo_objs)} insumos actualizados/creados.")

        # 2. Platos
        self.stdout.write("Sembrando platos del menú...")
        platos_data = [
            ("Lomo a lo Pobre", 8500.0),
            ("Hamburguesa Italiana", 6200.0),
            ("Churrasco Italiano", 5800.0),
            ("Completo Italiano", 2800.0),
            ("Porción Papas Fritas", 3000.0),
            ("Pechuga a la Plancha", 5500.0),
            ("Bebida en Lata", 1500.0),
        ]

        plato_objs = {}
        for nombre, valor in platos_data:
            pl, _ = Plato.objects.update_or_create(
                nombre=nombre,
                defaults={"valor": valor},
            )
            plato_objs[nombre] = pl

        self.stdout.write(f"  -> {len(plato_objs)} platos actualizados/creados.")

        # 3. Recetas (Escandallo)
        self.stdout.write("Vinculando escandallo y recetas (RecetaItem)...")
        recetas_data = [
            # Lomo a lo Pobre: Carne 0.300 kg, Papas 0.300 kg, Huevos 2 un, Cebolla 0.150 kg, Aceite 0.050 lt
            ("Lomo a lo Pobre", "INS-CARNE", Decimal("0.300")),
            ("Lomo a lo Pobre", "INS-PAPA", Decimal("0.300")),
            ("Lomo a lo Pobre", "INS-HUEVO", Decimal("2.000")),
            ("Lomo a lo Pobre", "INS-CEBOLLA", Decimal("0.150")),
            ("Lomo a lo Pobre", "INS-ACEITE", Decimal("0.050")),
            # Hamburguesa Italiana: Pan Frica 1 un, Carne 0.180 kg, Palta 0.100 kg, Tomate 0.080 kg, Mayo 0.030 lt
            ("Hamburguesa Italiana", "INS-PAN-FRICA", Decimal("1.000")),
            ("Hamburguesa Italiana", "INS-CARNE", Decimal("0.180")),
            ("Hamburguesa Italiana", "INS-PALTA", Decimal("0.100")),
            ("Hamburguesa Italiana", "INS-TOMATE", Decimal("0.080")),
            ("Hamburguesa Italiana", "INS-MAYO", Decimal("0.030")),
            # Churrasco Italiano: Pan Marraqueta 1 un, Carne 0.150 kg, Palta 0.100 kg, Tomate 0.080 kg, Mayo 0.030 lt
            ("Churrasco Italiano", "INS-PAN-MARRAQ", Decimal("1.000")),
            ("Churrasco Italiano", "INS-CARNE", Decimal("0.150")),
            ("Churrasco Italiano", "INS-PALTA", Decimal("0.100")),
            ("Churrasco Italiano", "INS-TOMATE", Decimal("0.080")),
            ("Churrasco Italiano", "INS-MAYO", Decimal("0.030")),
            # Completo Italiano: Pan Marraqueta 0.5 un, Vianesa 1 un, Tomate 0.060 kg, Palta 0.060 kg, Mayo 0.025 lt
            ("Completo Italiano", "INS-VIANESA", Decimal("1.000")),
            ("Completo Italiano", "INS-TOMATE", Decimal("0.060")),
            ("Completo Italiano", "INS-PALTA", Decimal("0.060")),
            ("Completo Italiano", "INS-MAYO", Decimal("0.025")),
            # Porción Papas Fritas: Papas 0.350 kg, Aceite 0.040 lt
            ("Porción Papas Fritas", "INS-PAPA", Decimal("0.350")),
            ("Porción Papas Fritas", "INS-ACEITE", Decimal("0.040")),
            # Pechuga a la Plancha: Pollo 0.250 kg, Aceite 0.020 lt
            ("Pechuga a la Plancha", "INS-POLLO", Decimal("0.250")),
            ("Pechuga a la Plancha", "INS-ACEITE", Decimal("0.020")),
            # Bebida
            ("Bebida en Lata", "INS-BEBIDA", Decimal("1.000")),
        ]

        receta_count = 0
        for plato_nom, insumo_cod, cant in recetas_data:
            if plato_nom in plato_objs and insumo_cod in insumo_objs:
                RecetaItem.objects.update_or_create(
                    plato=plato_objs[plato_nom],
                    insumo=insumo_objs[insumo_cod],
                    defaults={"cantidad": cant},
                )
                receta_count += 1

        self.stdout.write(f"  -> {receta_count} items de receta configurados.")

        # 4. Combos / Menús
        self.stdout.write("Sembrando promociones y combos (Menu)...")
        combos_data = [
            ("Combo Burger Doble", 8500.0, ["Hamburguesa Italiana", "Porción Papas Fritas", "Bebida en Lata"]),
            ("Promo Completo Dúo", 5000.0, ["Completo Italiano", "Bebida en Lata"]),
        ]

        combo_objs = {}
        for c_nom, c_precio, c_platos in combos_data:
            combo = Menu.objects.filter(nombre=c_nom).first()
            if not combo:
                combo = Menu.objects.create(nombre=c_nom, precio_menus=c_precio)
            else:
                combo.precio_menus = c_precio
                combo.save()
            # Asignar platos
            platos_to_add = [plato_objs[p] for p in c_platos if p in plato_objs]
            combo.platos.set(platos_to_add)
            combo_objs[c_nom] = combo

        self.stdout.write(f"  -> {len(combo_objs)} combos configurados.")

        # 5. Historial de Órdenes Completadas (para previsión IA)
        self.stdout.write(f"Generando historial de ventas ({dias} días)...")
        canales = ["Local", "Delivery", "Whatsapp"]
        tipos_pago = ["Efectivo", "Tarj. Débito", "Transferencia", "Delivery"]

        hoy = timezone.now().date()
        orders_created = 0

        # Crear órdenes históricas por día si aún no existen
        for offset in range(dias):
            fecha_orden = hoy - datetime.timedelta(days=offset)
            cliente_prefix = f"Demo-{fecha_orden.strftime('%Y%m%d')}"

            # 2 a 4 órdenes por día
            num_ordenes_dia = 3
            for i in range(1, num_ordenes_dia + 1):
                cliente_nom = f"{cliente_prefix}-{i}"
                orden = Orden.objects.filter(cliente=cliente_nom).first()
                if not orden:
                    orden = Orden.objects.create(
                        cliente=cliente_nom,
                        canal_venta=canales[(offset + i) % len(canales)],
                        tipo_pago=tipos_pago[(offset + i) % len(tipos_pago)],
                        estado=Orden.ESTADO_COMPLETADA,
                        stock_descontado=True,
                        fecha_completada=timezone.now() - datetime.timedelta(days=offset),
                    )
                    # Forzar fecha de la orden para que el agregador histórico la reconozca
                    Orden.objects.filter(id=orden.id).update(fecha=fecha_orden)

                    # Agregar items a la orden
                    if i % 2 == 1:
                        # Orden directa de platos
                        OrdenItem.objects.create(
                            orden=orden,
                            plato=plato_objs["Lomo a lo Pobre"],
                            cantidad=1,
                        )
                        OrdenItem.objects.create(
                            orden=orden,
                            plato=plato_objs["Porción Papas Fritas"],
                            cantidad=1,
                        )
                    else:
                        # Orden con combo y platos
                        combo_ref = combo_objs.get("Combo Burger Doble")
                        if combo_ref:
                            OrdenItem.objects.create(
                                orden=orden,
                                menu=combo_ref,
                                cantidad=1,
                            )
                        OrdenItem.objects.create(
                            orden=orden,
                            plato=plato_objs["Completo Italiano"],
                            cantidad=2,
                        )

                    orden.monto_total = orden.calcular_total()
                    orden.save()
                    orders_created += 1

        self.stdout.write(f"  -> {orders_created} órdenes históricas completadas registradas.")
        self.stdout.write(self.style.SUCCESS("✓ Datos demo sembrados con éxito. Base lista para previsión de demanda IA."))
