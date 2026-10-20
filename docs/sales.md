# Ventas, clientes y cuentas por cobrar

Este módulo usa el catálogo, almacenes y ledger de inventario existentes. Una cotización, pedido, entrega, devolución, factura interna, nota de crédito y pago son documentos/eventos diferentes. Los importes se calculan en el servidor con `Decimal`, redondeo `ROUND_HALF_UP` a centavos por línea y descuentos/impuestos copiados en cada línea histórica.

## Permisos y alcance

Todas las rutas requieren sesión bearer y `X-Company-ID`. `sales:read` permite consultar clientes, cotizaciones, pedidos, disponibilidad, entregas, devoluciones, notas de crédito, facturas y pagos; se asigna a admin/operator/viewer. `sales:manage` crea y cambia documentos y se asigna a admin/operator. Cada referencia a cliente, producto, sucursal, almacén, pedido, entrega y factura se valida en la empresa activa. La API no borra documentos comerciales.

## Endpoints

| Método y ruta | Uso |
|---|---|
| GET/POST `/api/v1/sales/customers` | Buscar/paginar y crear clientes |
| GET/PATCH `/api/v1/sales/customers/{id}` | Consultar, editar o desactivar cliente |
| GET `/api/v1/sales/availability` | Existencia física, reservada y disponible por producto/almacén |
| GET/POST `/api/v1/sales/quotes` | Listar y crear cotizaciones |
| GET/PATCH `/api/v1/sales/quotes/{id}` | Consultar o editar solo borradores |
| POST `/api/v1/sales/quotes/{id}/send`, `/accept`, `/reject` | Transiciones de cotización |
| POST `/api/v1/sales/quotes/{id}/convert` | Convertir una cotización aceptada en un pedido una sola vez |
| GET `/api/v1/sales/quotes/{id}/events` | Historial con actor y fecha |
| GET/POST `/api/v1/sales/orders` | Listar y crear pedidos directos |
| GET `/api/v1/sales/orders/{id}` | Consultar pedido y cantidades cumplidas |
| POST `/api/v1/sales/orders/{id}/confirm`, `/cancel` | Reservar disponibilidad o liberar una reserva abierta |
| GET `/api/v1/sales/orders/{id}/events` | Historial del pedido |
| GET/POST `/api/v1/sales/orders/{id}/deliveries` | Listar o registrar entregas parciales idempotentes |
| GET `/api/v1/sales/deliveries/{id}` | Consultar entrega y operaciones de inventario |
| POST `/api/v1/sales/returns` | Registrar devolución trazable y nota de crédito opcional |
| GET `/api/v1/sales/returns/{id}` | Consultar devolución |
| GET `/api/v1/sales/credit-notes` | Consultar notas de crédito generadas por devoluciones ligadas a factura |
| GET/POST `/api/v1/sales/invoices` | Consultar y crear facturas internas por cantidades cumplidas no facturadas |
| GET `/api/v1/sales/invoices/{id}` | Consultar saldo, pagos, créditos y líneas |
| POST `/api/v1/sales/invoices/{id}/payments` | Registrar pago parcial/completo con `Idempotency-Key` |
| GET `/api/v1/sales/invoices/{id}/payments` | Consultar pagos |
| POST `/api/v1/sales/invoices/{id}/void` | Anular solo factura impagada y sin notas de crédito |

Colecciones admiten `limit`/`offset`; clientes aceptan `q`/`active`; cotizaciones y pedidos aceptan `status` y `customer_id`.

## Estados y reglas

- Cotización: `draft → sent → accepted|rejected`; si vence antes de aceptarse, queda `expired`. Solo `draft` se edita. Aceptar no mueve stock. La conversión actualiza cotización y crea pedido en la misma transacción, copia las líneas/precios y tiene una restricción única por cotización.
- Pedido: `draft → confirmed → partially_fulfilled → fulfilled`; un borrador no reserva. Confirmar bloquea por producto/almacén y comprueba existencia física menos reservas activas. La reserva se deriva de las cantidades aún no cumplidas en pedidos confirmados/parcialmente cumplidos. Cancelar un pedido abierto libera lo pendiente; las cantidades ya entregadas permanecen trazables y facturables.
- Política de disponibilidad: `disponible = físico - reservado`. Se reserva a nivel de almacén, no ubicación. Las salidas manuales, ajustes negativos, transferencias y devoluciones de proveedor toman el mismo bloqueo de capacidad e impiden consumir unidades reservadas. Los envíos del propio pedido pueden consumir su reserva. El endpoint `/sales/availability` presenta las tres cantidades.
- Entregas: cantidad no puede exceder el pedido pendiente; se requiere `Idempotency-Key`. El pedido, la entrega, el ledger y los eventos se confirman en una sola transacción. Servicios se marcan cumplidos al confirmar el pedido sin salida física. Productos se descuentan con movimientos inmutables; una devolución los repone con movimiento compensatorio.
- Devolución: referencia una línea de entrega, exige motivo y cantidad no devuelta. Si se liga a una factura con cantidad y saldo pendientes, crea nota de crédito interna y reduce cuentas por cobrar; no altera ni elimina la factura o el movimiento original. Los créditos de facturas ya pagadas requieren un proceso futuro de reembolso/saldo a favor.
- Factura: es documento comercial interno, emitido sobre cantidades cumplidas y aún no facturadas. La numeración es única por empresa. El estado de pago es `unpaid`, `partial`, `paid` o `void`; los pagos bloquean la factura en PostgreSQL y no exceden saldo neto de créditos. Anular requiere factura `unpaid`, sin pagos ni notas de crédito.
- El límite de crédito del cliente es opcional; la confirmación del pedido lo valida sumando saldo de facturas y compromisos abiertos bajo bloqueo de cliente.
- Esta API no emite, timbra ni valida CFDI u otros comprobantes fiscales oficiales, ni afirma cumplimiento fiscal mexicano.

## Ejemplos

Crear cotización; no enviar totales calculados:

```json
{
  "customer_id": 12,
  "number": "Q-2026-018",
  "currency_code": "MXN",
  "valid_until": "2026-11-15",
  "lines": [{"product_id": 9, "quantity": "2", "discount_percent": "5"}]
}
```

Convertir cotización aceptada:

```json
{"number": "SO-2026-018", "warehouse_id": 3}
```

Registrar una entrega parcial:

```http
POST /api/v1/sales/orders/18/deliveries
Idempotency-Key: delivery-so18-1
```

```json
{"lines": [{"order_line_id": 47, "quantity": "1"}], "notes": "Entrega parcial"}
```

## Migración y verificación

La migración `0008_sales` añade clientes, documentos de venta, notas de crédito y permisos RBAC sin eliminar tablas existentes. Las pruebas SQLite cubren cotización a pedido, cálculos, aislamiento de tenant/producto, disponibilidad y reservas, cancelación, entregas parciales/idempotentes, devoluciones/créditos, facturas y pagos. PostgreSQL añade una prueba concurrente para que dos pedidos no reserven la misma unidad; bloqueo real, migración y concurrencia deben ejecutarse contra un PostgreSQL descartable mediante `TEST_DATABASE_URL`.
