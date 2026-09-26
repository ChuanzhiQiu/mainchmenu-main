# Generated for Milestone M6 (R1) - Zero-Loss Multi-Tenant Complete Isolation Migration

import django.db.models.deletion
import django.db.models.manager
from django.db import migrations, models



def ensure_mainch_and_backfill(apps, schema_editor):
    """
    Backfills all historical records to the canonical 'mainch' tenant.
    Guarantees zero nulls remain before Phase 3 applies NOT NULL constraints.
    """
    Restaurante = apps.get_model('Menu', 'Restaurante')
    Plato = apps.get_model('Menu', 'Plato')
    Menu = apps.get_model('Menu', 'Menu')
    Insumo = apps.get_model('Menu', 'Insumo')
    Orden = apps.get_model('Menu', 'Orden')
    OrdenItem = apps.get_model('Menu', 'OrdenItem')
    RecetaItem = apps.get_model('Menu', 'RecetaItem')
    MovimientoStock = apps.get_model('Menu', 'MovimientoStock')

    # 1. Ensure MAINCH tenant exists
    mainch = Restaurante.objects.filter(slug='mainch').first()
    if not mainch:
        mainch = Restaurante.objects.first()
    if not mainch:
        mainch = Restaurante.objects.create(
            nombre="Mainch",
            slug="mainch",
            direccion="Valparaíso, Chile",
            telefono="+56 9 1234 5678",
            activo=True,
        )

    # 2. Defensively ensure parent models have no nulls
    Plato.objects.filter(restaurante__isnull=True).update(restaurante=mainch)
    Menu.objects.filter(restaurante__isnull=True).update(restaurante=mainch)
    Insumo.objects.filter(restaurante__isnull=True).update(restaurante=mainch)
    Orden.objects.filter(restaurante__isnull=True).update(restaurante=mainch)

    # 3. Backfill OrdenItem inheriting from parent Orden
    for item in OrdenItem.objects.filter(restaurante__isnull=True).select_related('orden'):
        if item.orden and item.orden.restaurante_id:
            item.restaurante_id = item.orden.restaurante_id
        else:
            item.restaurante_id = mainch.id
        item.save(update_fields=['restaurante'])

    # 4. Backfill RecetaItem inheriting from Plato or Insumo
    for rec in RecetaItem.objects.filter(restaurante__isnull=True).select_related('plato', 'insumo'):
        if rec.plato and rec.plato.restaurante_id:
            rec.restaurante_id = rec.plato.restaurante_id
        elif rec.insumo and rec.insumo.restaurante_id:
            rec.restaurante_id = rec.insumo.restaurante_id
        else:
            rec.restaurante_id = mainch.id
        rec.save(update_fields=['restaurante'])

    # 5. Backfill MovimientoStock inheriting from Orden or Insumo
    for mov in MovimientoStock.objects.filter(restaurante__isnull=True).select_related('orden', 'insumo'):
        if mov.orden and mov.orden.restaurante_id:
            mov.restaurante_id = mov.orden.restaurante_id
        elif mov.insumo and mov.insumo.restaurante_id:
            mov.restaurante_id = mov.insumo.restaurante_id
        else:
            mov.restaurante_id = mainch.id
        mov.save(update_fields=['restaurante'])

    # 6. Absolute safety net: ensure zero orphaned nulls remain
    OrdenItem.objects.filter(restaurante__isnull=True).update(restaurante=mainch)
    RecetaItem.objects.filter(restaurante__isnull=True).update(restaurante=mainch)
    MovimientoStock.objects.filter(restaurante__isnull=True).update(restaurante=mainch)


class Migration(migrations.Migration):

    dependencies = [
        ('Menu', '0015_restaurante_orden_detalles_entrega_and_more'),
    ]

    operations = [
        # ======================================================================
        # PHASE 1: Add new tenant foreign keys as NULLABLE columns
        # ======================================================================
        migrations.AddField(
            model_name='ordenitem',
            name='restaurante',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='orden_items',
                to='Menu.restaurante',
                verbose_name='Restaurante',
            ),
        ),
        migrations.AddField(
            model_name='recetaitem',
            name='restaurante',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='receta_items',
                to='Menu.restaurante',
                verbose_name='Restaurante',
            ),
        ),
        migrations.AddField(
            model_name='movimientostock',
            name='restaurante',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='movimientos_stock',
                to='Menu.restaurante',
                verbose_name='Restaurante',
            ),
        ),

        # ======================================================================
        # PHASE 2: RunPython backfill setting restaurante_id to MAINCH
        # ======================================================================
        migrations.RunPython(
            ensure_mainch_and_backfill,
            reverse_code=migrations.RunPython.noop,
        ),

        # ======================================================================
        # PHASE 3: Alter columns to NOT NULL and configure composite constraints
        # ======================================================================
        migrations.AlterField(
            model_name='ordenitem',
            name='restaurante',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name='orden_items',
                to='Menu.restaurante',
                verbose_name='Restaurante',
            ),
        ),
        migrations.AlterField(
            model_name='recetaitem',
            name='restaurante',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name='receta_items',
                to='Menu.restaurante',
                verbose_name='Restaurante',
            ),
        ),
        migrations.AlterField(
            model_name='movimientostock',
            name='restaurante',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name='movimientos_stock',
                to='Menu.restaurante',
                verbose_name='Restaurante',
            ),
        ),

        # Insumo SKU Evolution: Drop global unique, add composite constraint
        migrations.AlterField(
            model_name='insumo',
            name='codigo',
            field=models.CharField(
                help_text='Identificador único o SKU del insumo en el restaurante (ej: INS-001, INS-PAN).',
                max_length=50,
                verbose_name='Código Insumo',
            ),
        ),
        migrations.AddConstraint(
            model_name='insumo',
            constraint=models.UniqueConstraint(
                fields=('restaurante', 'codigo'),
                name='unique_restaurante_insumo_codigo',
            ),
        ),

        # Restaurante: Remove static defaults on unique fields to prevent collision
        migrations.AlterField(
            model_name='restaurante',
            name='nombre',
            field=models.CharField(max_length=100, unique=True),
        ),
        migrations.AlterField(
            model_name='restaurante',
            name='slug',
            field=models.SlugField(max_length=100, unique=True),
        ),
        migrations.AlterField(
            model_name='restaurante',
            name='activo',
            field=models.BooleanField(db_index=True, default=True),
        ),

        # Model Meta options: base_manager_name = 'all_objects'
        migrations.AlterModelOptions(
            name='insumo',
            options={'base_manager_name': 'all_objects', 'ordering': ['nombre'], 'verbose_name': 'Insumo', 'verbose_name_plural': 'Insumos'},
        ),
        migrations.AlterModelOptions(
            name='menu',
            options={'base_manager_name': 'all_objects', 'verbose_name': 'Menú', 'verbose_name_plural': 'Menús'},
        ),
        migrations.AlterModelOptions(
            name='movimientostock',
            options={'base_manager_name': 'all_objects', 'ordering': ['-fecha_hora', '-id'], 'verbose_name': 'Movimiento de Stock (Kardex)', 'verbose_name_plural': 'Movimientos de Stock (Kardex)'},
        ),
        migrations.AlterModelOptions(
            name='orden',
            options={'base_manager_name': 'all_objects', 'verbose_name': 'Orden', 'verbose_name_plural': 'Órdenes'},
        ),
        migrations.AlterModelOptions(
            name='ordenitem',
            options={'base_manager_name': 'all_objects', 'verbose_name': 'Ítem de Orden', 'verbose_name_plural': 'Ítems de Orden'},
        ),
        migrations.AlterModelOptions(
            name='plato',
            options={'base_manager_name': 'all_objects', 'verbose_name': 'Plato', 'verbose_name_plural': 'Platos'},
        ),
        migrations.AlterModelOptions(
            name='recetaitem',
            options={'base_manager_name': 'all_objects', 'ordering': ['plato', 'insumo'], 'verbose_name': 'Ítem de Receta / Escandallo', 'verbose_name_plural': 'Ítems de Receta / Escandallo'},
        ),
        migrations.AlterModelManagers(
            name='insumo',
            managers=[
                ('objects', django.db.models.manager.Manager()),
                ('all_objects', django.db.models.manager.Manager()),
            ],
        ),
        migrations.AlterModelManagers(
            name='menu',
            managers=[
                ('objects', django.db.models.manager.Manager()),
                ('all_objects', django.db.models.manager.Manager()),
            ],
        ),
        migrations.AlterModelManagers(
            name='movimientostock',
            managers=[
                ('objects', django.db.models.manager.Manager()),
                ('all_objects', django.db.models.manager.Manager()),
            ],
        ),
        migrations.AlterModelManagers(
            name='orden',
            managers=[
                ('objects', django.db.models.manager.Manager()),
                ('all_objects', django.db.models.manager.Manager()),
            ],
        ),
        migrations.AlterModelManagers(
            name='ordenitem',
            managers=[
                ('objects', django.db.models.manager.Manager()),
                ('all_objects', django.db.models.manager.Manager()),
            ],
        ),
        migrations.AlterModelManagers(
            name='plato',
            managers=[
                ('objects', django.db.models.manager.Manager()),
                ('all_objects', django.db.models.manager.Manager()),
            ],
        ),
        migrations.AlterModelManagers(
            name='recetaitem',
            managers=[
                ('objects', django.db.models.manager.Manager()),
                ('all_objects', django.db.models.manager.Manager()),
            ],
        ),
    ]

