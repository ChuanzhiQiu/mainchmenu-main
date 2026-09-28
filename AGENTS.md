
## 7. Directrices Estrictas para Django REST Framework (DRF)
- **Serializers:**
  - El campo `restaurante` DEBE declararse como `read_only=True` o ser omitido de `fields`. Jamás permitir que el cliente lo envíe o modifique por payload.
- **ViewSets / Vistas:**
  - NUNCA usar `queryset = Modelo.objects.all()` sin filtrar.
  - Sobrescribir obligatoriamente `get_queryset()` para aislar los datos:
    `return Modelo.objects.filter(restaurante=self.request.user.cajero.restaurante)` (o el método de resolución de tenant vigente).
  - Sobrescribir `perform_create(serializer)` para inyectar automáticamente el tenant:
    `serializer.save(restaurante=...)`.
- **Validación Cruzada en Relaciones (FKs):**
  - Al recibir llaves foráneas en payloads (ej. asignar `area` a una `Mesa`), validar que la entidad relacionada pertenezca al mismo restaurante antes de guardar.
- **Acciones Críticas (Cobro / Pagos):**
  - Usar decoradores `@action(detail=True, methods=['post'], url_path='registrar-pago')`.
  - Conectar directamente a la capa de servicio `pago_service.registrar_pago(...)` envolviendo en manejo de excepciones de dominio.

## 8. Delegación al Subagente Worker (Qwen 3B)
Tienes a tu disposición un subagente local rápido para boilerplate mediante el comando:
`python scripts/qwen_worker.py "<especificacion de codigo>"`

- **Flujo de trabajo autónomo:**
  1. No escribas código repetitivo desde cero si puedes delegarlo.
  2. Ejecuta el worker pasándole las firmas requeridas (ej: `python scripts/qwen_worker.py "Escribe CategoriaViewSet..." > Menu/views.py`).
  3. Lee e inspecciona la salida generada.
  4. Audita y aplica las reglas de seguridad multi-tenant de la Sección 7.
  5. Corre los tests para verificar.
