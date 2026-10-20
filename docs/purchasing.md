# Compras, proveedores y cuentas por pagar

El módulo usa los productos actuales del catálogo y los almacenes del módulo de inventario. Una orden, una recepción física, una devolución, una factura y un pago son documentos separados. El backend calcula importes; los totales enviados por clientes no forman parte de los esquemas de entrada.

## Permisos

Todas las rutas requieren bearer token y X-Company-ID.

- purchasing:read permite consultar proveedores (sin notas internas), órdenes, historial, recepciones, devoluciones, facturas y pagos. Se asigna a admin, operator y viewer.
- purchasing:manage permite crear y modificar proveedores, borradores, órdenes, recepciones, devoluciones, facturas y pagos; también leer el detalle de proveedor con notas internas. Se asigna a admin y operator.

Cada referencia a proveedor, producto, sucursal, almacén, orden, recepción, línea o factura se valida dentro de la empresa activa. Proveedores y documentos se desactivan o anulan con estados; no se borran desde la API.

## Rutas

| Método y ruta | Acción |
|---|---|
| GET/POST /api/v1/purchasing/suppliers | Buscar, paginar y crear proveedores |
| GET/PATCH /api/v1/purchasing/suppliers/{id} | Detalle controlado y actualización/desactivación lógica |
| GET/POST /api/v1/purchasing/orders | Consultar y crear borradores |
| GET/PATCH /api/v1/purchasing/orders/{id} | Consultar o editar borradores |
| POST /api/v1/purchasing/orders/{id}/confirm | Confirmar borrador no vacío |
| POST /api/v1/purchasing/orders/{id}/cancel | Cancelar borrador u orden confirmada sin recepciones |
| POST /api/v1/purchasing/orders/{id}/close | Cerrar orden confirmada al completar bienes |
| GET /api/v1/purchasing/orders/{id}/events | Historial de cambios con usuario y fecha |
| GET/POST /api/v1/purchasing/orders/{id}/receipts | Consultar y registrar recepciones |
| GET /api/v1/purchasing/receipts/{id} | Consultar recepción |
| POST /api/v1/purchasing/receipts/{id}/returns | Devolver bienes aceptados |
| GET /api/v1/purchasing/returns/{id} | Consultar devolución |
| GET/POST /api/v1/purchasing/invoices | Consultar o registrar facturas recibidas |
| GET /api/v1/purchasing/invoices/{id} | Consultar factura y líneas |
| GET/POST /api/v1/purchasing/invoices/{id}/payments | Consultar pagos o registrar pagos parciales/completos |
| POST /api/v1/purchasing/invoices/{id}/void | Anular factura impagada y sin pagos |

Las colecciones aceptan límite y desplazamiento; proveedores permiten q y active, órdenes status y supplier_id, recepciones purchase_order_id e invoices supplier_id.

## Reglas

- Una orden nueva queda en draft. Solo borradores se editan; confirm sella su contenido para edición ordinaria. Puede cancelarse un borrador o una confirmada sin recepciones. Si existe una recepción, la corrección es una devolución. close requiere que no queden cantidades de bienes pendientes; los servicios no requieren recepción física.
- Las líneas de orden apuntan al producto existente y guardan descripción/precio/descuento/impuesto como snapshot. Importes redondeados a dos decimales con ROUND_HALF_UP por línea. Cantidades usan hasta seis decimales según allows_fractional_quantity del producto.
- Una recepción requiere orden confirmada. accepted_quantity aumenta stock en el almacén destino de la orden y ubicación elegida; damaged_quantity se registra, pero no entra a existencias. La suma no puede exceder lo pendiente.
- Cabecera, líneas de recepción, cantidades recibidas, operaciones del ledger, movimientos y auditoría comparten una transacción. Un error revierte todo. Idempotency-Key único por empresa devuelve el recibo anterior si el payload coincide y conflicto si cambió. Cada entrada usa además una clave determinista de inventario y referencia al recibo.
- Devoluciones exigen motivo, referencia a línea aceptada y cantidad aún no devuelta. Generan salida de inventario desde la ubicación original mediante movimiento compensatorio; la disponibilidad se comprueba con el ledger.
- Una factura registra un documento recibido; no emite ni valida CFDI u otros documentos fiscales oficiales. El número es único por proveedor y empresa. Sus líneas calculan importes en el servidor y pueden referenciar líneas de orden y/o recepción de la misma empresa.
- Pagos son positivos, de dos decimales e idempotentes; no pueden exceder saldo. La factura avanza unpaid a partial y luego paid. Solo se anula como void si no tiene pagos. PostgreSQL bloquea la factura al aplicar el pago.
- internal_notes se excluye de resultados de lista y lectura ordinaria; solo se devuelve en detalle con purchasing:manage.

### Crear borrador

Enviar proveedor, sucursal opcional, almacén, número, moneda y líneas con producto, cantidad, precio, descuento e impuesto. No enviar subtotal, tax_total ni total. El servidor calcula los importes.

### Recibir parcialmente

POST /api/v1/purchasing/orders/42/receipts con Idempotency-Key: receive-oc42-1 y JSON como:

    {
      "lines": [
        {
          "purchase_order_line_id": 77,
          "accepted_quantity": "10",
          "damaged_quantity": "2",
          "location_id": 9,
          "notes": "Dos piezas con empaque abierto"
        }
      ]
    }

### Factura recibida

Registrar proveedor, número externo, fechas, moneda, líneas e IDs internos de orden/recepción cuando correspondan. El número de proveedor no se interpreta como un comprobante fiscal validado.

## Verificación

SQLite cubre cálculo y redondeo, aislamiento, estados, recepciones parciales/dañadas, idempotencia, devoluciones, duplicados de factura, pagos parciales y rollback de una recepción si falla una línea. PostgreSQL tiene pruebas para salidas concurrentes de inventario y pagos concurrentes que podrían exceder saldo. Solo la ejecución contra un servidor real valida esos bloqueos.
