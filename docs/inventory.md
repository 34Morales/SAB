# Inventario trazable

El inventario comparte `products` con el catálogo y registra cantidades en un libro inmutable. No hay un campo editable de stock en el producto. `inventory_movements` es la fuente de verdad; `inventory_balances` es una proyección reconstruible que se actualiza junto con el libro en la misma transacción.

## API

Todas las rutas requieren sesión bearer, `X-Company-ID` y permisos concedidos para esa membresía.

| Método y ruta | Permiso | Uso |
|---|---|---|
| `GET /api/v1/inventory/warehouses` | `inventory:read` | Almacenes de la empresa activa |
| `POST /api/v1/inventory/warehouses` | `inventory:manage` | Crear almacén, opcionalmente ligado a sucursal |
| `PATCH /api/v1/inventory/warehouses/{id}/deactivate` | `inventory:manage` | Desactivar si sus saldos son cero |
| `GET /api/v1/inventory/locations?warehouse_id=1` | `inventory:read` | Ubicaciones internas del almacén |
| `POST /api/v1/inventory/locations` | `inventory:manage` | Crear ubicación en un almacén de la empresa |
| `POST /api/v1/inventory/operations` | `inventory:manage` | Registrar entrada, salida, transferencia o ajuste |
| `GET /api/v1/inventory/balances` | `inventory:read` | Saldos; filtros `warehouse_id`, `branch_id`, `product_id` y paginación |
| `GET /api/v1/inventory/balances/below-minimum` | `inventory:read` | Posiciones bajo el mínimo configurado |
| `PATCH /api/v1/inventory/balances/{id}/thresholds` | `inventory:manage` | Definir mínimo y punto de reorden |
| `GET /api/v1/inventory/movements` | `inventory:read` | Kardex paginado por producto/almacén, con referencia, motivo y responsable |
| `GET /api/v1/inventory/summary` | `inventory:read` | Resumen por producto, almacén y ubicación |

### Ejemplo de entrada

```http
POST /api/v1/inventory/operations
Authorization: Bearer <token>
X-Company-ID: 12
Idempotency-Key: receipt-order-4501
Content-Type: application/json
```

```json
{
  "kind": "receipt",
  "product_id": 81,
  "warehouse_id": 4,
  "quantity": "25.000000",
  "unit_cost": "18.450000",
  "reason": "Recepción de compra",
  "reference_type": "purchase_order",
  "reference_id": "4501"
}
```

Para transferencias se requiere `destination_warehouse_id`; ambas patas nacen en una sola transacción. Ajustes requieren motivo. Reutilizar una clave idempotente con el mismo contenido devuelve la operación previa; usarla con otro contenido produce conflicto. Cantidades y costos son decimales; fracciones solo se permiten en productos con `allows_fractional_quantity=true`.

## Consistencia y seguridad

- Cada movimiento referencia empresa, producto, almacén, ubicación opcional y operación. La operación guarda tipo, referencia, motivo, observaciones, usuario y fecha.
- Triggers SQLite/PostgreSQL rechazan cambios y borrados de operaciones o movimientos confirmados. Las correcciones deben ser otro movimiento de ajuste.
- PostgreSQL toma advisory transaction locks por clave idempotente y posición, más `FOR UPDATE` en saldos. Las posiciones de una transferencia se bloquean en orden estable. SQLite mantiene atomicidad para las pruebas locales, pero no acredita concurrencia productiva.
- El kardex, evento de seguridad y saldo proyectado se guardan en la misma transacción.
- `reconcile_balances(session, company_id)` compara ledger y proyección y devuelve diferencias. `repair=True` reconstruye la cantidad proyectada de posiciones con movimientos; debe usarse como operación administrativa controlada.
- No se crean productos ni almacenes de demostración automáticamente; se usan productos existentes del catálogo.

La prueba PostgreSQL concurrente intenta dos salidas de siete unidades sobre una existencia de diez y espera exactamente una confirmación. Solo acredita concurrencia real cuando se ejecuta con `TEST_DATABASE_URL` hacia PostgreSQL.
