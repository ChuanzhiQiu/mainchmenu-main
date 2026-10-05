"""
Management command: migrar_menu
Lee el menú digitalizado (Menu/fixtures/menu_migracion.json) y crea/actualiza
el catálogo multi-tenant: Categoria, Plato y Menú (combos).
Idempotente: puede re-ejecutarse sin duplicar registros.
"""
import json
from decimal import Decimal
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from Menu.models import Categoria, Menu, Plato, Restaurante


class Command(BaseCommand):
    help = "Migra el catálogo (Categoria, Plato, Menú combos) desde el JSON del menú digitalizado."

    def add_arguments(self, parser):
        parser.add_argument(
            "--fixture",
            default=str(Path("Menu/fixtures/menu_migracion.json")),
            help="Ruta al JSON del menú digitalizado.",
        )
        parser.add_argument(
            "--tenant-slug",
            default="mainch",
            help="Slug del restaurante al que se asigna el catálogo (por defecto: mainch).",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        fixture_path = Path(options["fixture"])
        if not fixture_path.exists():
            raise CommandError(f"No se encontró el fixture: {fixture_path}")

        data = json.loads(fixture_path.read_text(encoding="utf-8"))

        restaurante, _ = Restaurante.objects.get_or_create(
            slug=options["tenant_slug"],
            defaults={"nombre": "Mainch", "direccion": "Valparaíso, Chile"},
        )
        self.stdout.write(f"Tenant: {restaurante.nombre} (id={restaurante.id})")

        # 1. Categorías
        categorias = {}
        for idx, c in enumerate(data.get("categorias", [])):
            cat, _ = Categoria.objects.update_or_create(
                restaurante=restaurante,
                nombre=c["nombre"],
                defaults={"orden": idx, "activo": True},
            )
            categorias[c["nombre"]] = cat
        self.stdout.write(self.style.SUCCESS(f"Categorías: {len(categorias)}"))

        # 2. Platos
        platos_creados = platos_actualizados = 0
        platos = {}
        for p in data.get("platos", []):
            cat = categorias.get(p.get("categoria"))
            if cat is None:
                raise CommandError(
                    f"Categoría desconocida '{p.get('categoria')}' para el plato '{p['nombre']}'."
                )
            obj, created = Plato.objects.update_or_create(
                restaurante=restaurante,
                nombre=p["nombre"],
                defaults={
                    "valor": Decimal(str(p["precio"])),
                    "categoria": cat,
                    "descripcion": p.get("descripcion", ""),
                },
            )
            platos[p["nombre"]] = obj
            if created:
                platos_creados += 1
            else:
                platos_actualizados += 1
        self.stdout.write(
            self.style.SUCCESS(
                f"Platos: {platos_creados} creados, {platos_actualizados} actualizados"
            )
        )

        # 3. Combos (Menú)
        combos_creados = combos_actualizados = 0
        for c in data.get("combos", []):
            componentes = []
            for nombre_plato in c.get("platos", []):
                if nombre_plato not in platos:
                    raise CommandError(
                        f"Plato no encontrado para el combo '{c['nombre']}': {nombre_plato}"
                    )
                componentes.append(platos[nombre_plato])

            combo, created = Menu.objects.update_or_create(
                restaurante=restaurante,
                nombre=c["nombre"],
                defaults={
                    "precio_menus": Decimal(str(c["precio"])),
                    "descripcion": c.get("detalle", ""),
                },
            )
            combo.platos.set(componentes)
            if created:
                combos_creados += 1
            else:
                combos_actualizados += 1
        self.stdout.write(
            self.style.SUCCESS(
                f"Combos: {combos_creados} creados, {combos_actualizados} actualizados"
            )
        )

        self.stdout.write(self.style.SUCCESS("Migración de catálogo completada."))
