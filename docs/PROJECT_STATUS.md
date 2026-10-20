# Estado del proyecto SAB

Este documento diferencia código existente de funcionalidades verificadas. La suite local usa SQLite; la prueba PostgreSQL no se considera aprobada hasta que se ejecute contra una instancia descartable real.

## Completado y verificado

- [x] Base modular FastAPI, SQLAlchemy 2, Pydantic y Alembic.
- [x] Migraciones de núcleo de empresas/identidad y controles de seguridad; la prueba SQLite valida base vacía y adopción de una tabla `companies` previa.
- [x] Usuarios, membresías por empresa, roles/permisos base, sesiones bearer con digest, expiración y revocación.
- [x] Límite persistente de intentos de autenticación, MFA TOTP con secretos cifrados, códigos de recuperación de un solo uso, restablecimiento de contraseña y auditoría append-only con triggers SQLite.
- [x] Pruebas locales de hashing, TOTP, migraciones, autenticación, MFA, recuperación, SMTP simulado, auditoría y dependencias de autorización multiempresa.
- [x] El modo producción rechaza SQLite, secretos inválidos/placeholder, CORS comodín y URL pública sin HTTPS; CORS no permite credenciales.
- [x] Administración inicial de sucursales: listado, alta y edición/desactivación con permisos, unicidad por empresa, aislamiento por empresa y auditoría.
- [x] Configuración regional por empresa: país, moneda, zona IANA, locale e identificador fiscal; lectura/cambios limitados al contexto activo y auditados.
- [x] Catálogo inicial por empresa: categorías y productos/servicios, permisos de lectura/gestión, SKU único, validación decimal, filtros/paginación, aislamiento en lectura/escritura y eventos auditables.
- [x] Inventario por empresa: almacenes ligados opcionalmente a sucursal, ubicaciones, kardex inmutable, saldos proyectados, entradas/salidas/transferencias/ajustes, referencias/costos/motivos, idempotencia, cantidades fraccionarias y política empresarial explícita de stock negativo.
- [x] API de almacenes/ubicaciones, operaciones, saldos filtrables, mínimos/reorden, kardex y resumen; verificación de pertenencia empresarial, permisos y auditoría.
- [x] Reconciliación del saldo proyectado desde el ledger; transferencias atómicas y bloqueos PostgreSQL implementados.
- [x] Pruebas locales SQLite de operaciones, aislamiento, duplicados, stock, ledger inmutable y reconciliación. Prueba PostgreSQL concurrente añadida, pendiente de ejecución real.
- [x] Administración de membresías por empresa: lectura, activación/desactivación, asignación de roles globales o del tenant, protección del último administrador activo y auditoría.
- [x] Roles personalizados por empresa: alta/edición con el catálogo de permisos existente; roles globales de sistema y roles de otros tenants no son modificables desde la API.
- [x] Borrado seguro de roles personalizados sin asignaciones; la API rechaza roles asignados y no expone roles globales del sistema para borrado. Las denegaciones quedan auditadas.
- [x] Compras y proveedores por empresa: CRUD y desactivación lógica de proveedores, órdenes con estados e historial, recepciones parciales integradas transaccionalmente con el ledger de inventario, devoluciones compensatorias, facturas recibidas y pagos parciales idempotentes.
- [x] Permisos purchasing:read/manage, validación de referencias dentro del tenant, auditoría de eventos/rechazos y notas internas protegidas; migración Alembic 0007 y documentación de rutas/reglas en [docs/purchasing.md](purchasing.md).
- [x] Suite SQLite de compras: redondeo, límites de cantidades, aislamiento multiempresa, permisos, transiciones, recepción parcial/dañada, reintentos idempotentes, devolución, factura duplicada, pagos y rollback ante fallo de inventario.
- [x] Ventas/clientes por empresa: clientes con crédito opcional, cotizaciones con historial/vencimiento, conversión única a pedido, precios e impuestos calculados en backend y permisos sales:read/manage.
- [x] Reservas por producto/almacén derivadas de pedidos abiertos; consulta de físico/reservado/disponible, protección de salidas manuales, cancelación con liberación, entregas idempotentes/atómicas, devoluciones compensatorias, notas de crédito internas y pagos parciales.
- [x] Migración Alembic 0008, documentación [docs/sales.md](sales.md), pruebas SQLite de ciclo comercial, crédito, aislamiento, idempotencia y reservas. Prueba PostgreSQL concurrente añadida, pendiente de ejecución real.
- [x] Guía operativa inicial para configuración de secretos, migraciones, bootstrap, worker SMTP y estrategia de backup/restore; procedimientos descritos, pendientes de ensayo en infraestructura real.

## Implementado, pendiente de verificación

- [ ] Migraciones PostgreSQL, triggers, compras, ventas/reservas y flujos de autenticación/inventario: existen pruebas de integración con esquema aislado y limpieza, pero no se ejecutaron en este equipo.
- [ ] Envío SMTP real con el proveedor y certificados del despliegue.
- [ ] Comportamiento tras proxy inverso, configuración TLS de PostgreSQL y secretos del gestor operativo.
- [ ] Restauración de respaldos y prueba de recuperación con una base de datos desplegada.

## En desarrollo

- [ ] Completar la administración global de usuarios: suspensión global y flujo de baja/anonimización con política de retención para auditoría y documentos comerciales.
- [ ] Configurar lint, formato y tipado estático. `ruff`, `mypy` y `pyright` no están instalados en el entorno; no se descargaron herramientas adicionales.
- [ ] Definir versiones bloqueadas o un lockfile para instalación reproducible; hoy `pyproject.toml` usa rangos compatibles.
- [ ] Ampliar configuración fiscal por país y catálogos parametrizables; lo actual guarda identificador fiscal y región/moneda, sin validar obligaciones fiscales.
- [ ] Completar catálogos de unidades/impuestos parametrizables y reglas fiscales por país; los porcentajes actuales son snapshots internos y no garantizan cumplimiento fiscal.

## Bloqueado por dependencias externas

- [ ] Ejecutar PostgreSQL real: no se encuentran `psql`, `pg_isready`, `postgres`, `initdb`, `pg_ctl`, Docker ni Podman; WSL está presente como comando, pero la característica no está instalada. La consulta al Service Control Manager de Windows fue denegada por permisos del entorno. Se necesita una instancia PostgreSQL accesible mediante `TEST_DATABASE_URL` o autorización para habilitar un runtime local confiable.
- [ ] Pruebas con SMTP real: faltan credenciales y servidor configurados.
- [ ] CI alojada: workflow de GitHub Actions con PostgreSQL 16 configurado; la API de GitHub devolvió cero ejecuciones del repositorio el 20 de octubre de 2026. Este cambio local requiere publicarse para disparar CI.

## Pendiente de revisión especializada

- [ ] Revisión independiente de seguridad antes de producción.
- [ ] Política de retención, exportación y réplica externa de auditoría.
- [ ] Requisitos fiscales, contables, laborales y de protección de datos mexicanos.
- [ ] Validación operativa de backups cifrados y restauración.

## Etapas funcionales siguientes

- [ ] Contabilidad y conciliación entre recepción, factura y orden (matching de 2/3 vías). Compras y ventas registran vínculos comerciales, pero aún no imponen matching contable.
- [ ] Recursos humanos y reportes.
- [ ] Frontend React/TypeScript; hoy no existe frontend.
- [ ] Docker Compose y CI reproducible; la guía operativa inicial existe, falta completar y validar la guía de despliegue para infraestructura elegida.
- [ ] Pruebas de proceso empresarial de extremo a extremo.

## Verificación local más reciente

Ejecutado el 20 de octubre de 2026: `16 passed, 4 skipped in 20.70s`; los cuatro skips son las pruebas PostgreSQL de identidad, salidas de inventario, pagos a proveedores y reservas de pedidos, porque falta `TEST_DATABASE_URL`. También pasaron `compileall`, comprobación OpenAPI (73 rutas, 24 de ventas), `git diff --check` y búsqueda de espacios finales en archivos modificados. Una omisión no equivale a aprobación. La API de GitHub había devuelto cero ejecuciones de Actions; el cambio local aún no dispara CI remota.

Comando:

```powershell
.\.venv\Scripts\python.exe -m pytest -q --basetemp=.pytest-tmp-sales-final
```

Las pruebas PostgreSQL se omiten explícitamente cuando no hay `TEST_DATABASE_URL`. Una omisión no equivale a aprobación. El workflow de Actions usa PostgreSQL 16, pero aún no hay una ejecución remota.
