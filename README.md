# ERP Empresarial

ERP independiente en desarrollo. No es un producto oficial ni una implementación compatible con SAP.

## Estado

Estado del corte: API FastAPI modular con empresas/sucursales, catálogo, inventario trazable, compras/cuentas por pagar, ventas/clientes y cuentas por cobrar, membresías/roles administrables, autenticación, MFA, recuperación y auditoría. No está listo para producción: las pruebas PostgreSQL no se ejecutaron localmente (faltó TEST_DATABASE_URL), no hay ejecuciones remotas de Actions aún y faltan revisión independiente y verificación de SMTP.

## Requisitos y ejecución local

- Python 3.12 o posterior (entorno inspeccionado: Python 3.14).
- PostgreSQL para uso compartido; SQLite se usa por defecto para facilitar el arranque local.
- Docker no está instalado en el entorno inspeccionado; Compose queda pendiente.

Desde la raíz del repositorio:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .\backend[dev]
Copy-Item .env.example .env
.\.venv\Scripts\alembic.exe -c backend/alembic.ini upgrade head
.\.venv\Scripts\python.exe -m uvicorn main:app --reload
```

Crear la primera empresa y administrador (solo tras migrar) desde una terminal local:

```powershell
.\.venv\Scripts\python.exe -m backend.app.cli
```

El comando solicita empresa, nombre, correo y contraseña sin imprimir la contraseña. Para llamadas API autenticadas, envía `Authorization: Bearer <token>` y `X-Company-ID: <id de empresa>`. Inicia sesión con `POST /api/v1/auth/login`; enviar `X-Company-ID` también atribuye el evento de autenticación a esa empresa. La respuesta contiene un token aleatorio opaco de 8 horas, cuyo digest se guarda en la base de datos. `POST /api/v1/auth/logout` revoca la sesión. El hash de contraseñas usa PBKDF2-HMAC-SHA256 con sal aleatoria y 600,000 iteraciones.

## Controles de identidad y seguridad

- **Límite de intentos:** contador atómico persistido en PostgreSQL/SQLite, ventana de 15 minutos y bloqueo de 15 minutos al quinto fallo por combinación de correo/IP o al trigésimo por IP. Se devuelve `429` con `Retry-After`. También cubre la confirmación y desactivación de MFA. La clave del contador es un HMAC con `AUTH_THROTTLE_PEPPER`; usa un secreto aleatorio distinto por instalación.
- **MFA TOTP:** `POST /api/v1/auth/mfa/setup` entrega el secreto y URI de configuración una sola vez; confirma con `/auth/mfa/confirm`. Se aceptan TOTP de 6 dígitos con una ventana de ±30 segundos y no se reutiliza el paso ya aceptado. La activación entrega diez códigos de recuperación de un solo uso, que el usuario debe guardar fuera del sistema. El MFA se puede quitar con `/auth/mfa/disable` presentando la contraseña y un TOTP o código de recuperación. `MFA_ENCRYPTION_KEY` debe ser una clave Fernet estable, compartida por instancias y protegida como secreto.
- **Recuperación de contraseña:** `/auth/password-recovery` siempre responde con un mensaje genérico y encola el correo si la cuenta existe. `/auth/password-reset` consume un token de 20 minutos, cambia el hash y revoca todas las sesiones anteriores. El token se guarda en forma digestada; la copia para entrega se cifra en el outbox. Configura SMTP con TLS y ejecuta `python -m backend.app.modules.identity.mailer` como tarea de fondo. El envío tiene reintentos limitados y no registra enlaces ni credenciales. La URL utiliza un fragmento para que el token no viaje en solicitudes HTTP de recursos de la página.
- **Auditoría:** intentos y éxitos de autenticación, MFA, recuperación, alta de usuarios y empresas se registran sin contraseñas, tokens, códigos MFA ni correo sin procesar. `GET /api/v1/security/events` requiere permiso `security:audit:read` y limita resultados a la empresa activa. Triggers de SQLite y PostgreSQL rechazan actualizaciones y eliminaciones. Un administrador de base de datos con privilegios elevados aún puede alterar o desactivar esos controles.
- **CORS y CSRF:** CORS queda desactivado salvo que se configure una lista de orígenes. La API no usa cookies de sesión y CORS no habilita credenciales; las rutas protegidas requieren un bearer token explícito. Para clientes web, evita guardar tokens de larga duración en almacenamiento accesible a JavaScript y sirve la aplicación por HTTPS.
- **Empresa activa:** `GET/PATCH /api/v1/companies/current` consulta o cambia datos fiscales/regionales de la membresía activa. Moneda (ISO 4217), zona horaria IANA y locale se validan; los cambios quedan auditados. El país sigue siendo ISO 3166-1 alpha-2. No incluye todavía parametrización fiscal mexicana.
- **Catálogo:** categorías y productos/servicios en `/api/v1/catalog`, siempre filtrados por empresa activa. Admin gestiona; operator/viewer leen. SKU único por empresa, cantidades monetarias decimales, precio e impuesto no negativos y desactivación lógica. El catálogo no almacena existencias ni declara que el porcentaje de impuesto cumpla reglas fiscales.
- **Inventario:** detalle de endpoints, entradas/salidas, transferencias, ajustes, idempotencia, saldos, ubicaciones y reconciliación en [docs/inventory.md](docs/inventory.md). El ledger no permite editar ni borrar movimientos confirmados; el saldo se bloquea en transacciones PostgreSQL.
- **Compras y proveedores:** ciclo separado de órdenes, recepciones físicas integradas con inventario, devoluciones trazables, facturas recibidas y pagos parciales. Totales calculados en servidor, permisos por tenant, notas internas restringidas e idempotencia para recepciones/devoluciones/pagos. Rutas, estados, ejemplos y límites en [docs/purchasing.md](docs/purchasing.md). No emite ni valida documentos fiscales oficiales.
- **Ventas y clientes:** cotizaciones convertibles, pedidos con reservas, consulta físico/reservado/disponible, entregas parciales, devoluciones con notas de crédito internas, facturación sobre cantidades cumplidas y pagos parciales. Bloquea salidas para proteger stock reservado e incluye límite de crédito opcional. Rutas y reglas en [docs/sales.md](docs/sales.md). No emite ni valida CFDI.
- **Membresías y roles:** `GET/PATCH /api/v1/identity/memberships` gestiona el acceso por empresa; `POST/PATCH/DELETE /api/v1/identity/roles` gestiona roles personalizados. La API impide editar o borrar roles globales, borrar roles asignados, asignar recursos de otro tenant y quitar al último administrador activo.

Para generar secretos localmente, luego de instalar dependencias:

```powershell
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Guarda los valores resultantes en el gestor de secretos del entorno, nunca en Git. `ENVIRONMENT=production` exige ambos secretos, SMTP con TLS, una URL HTTPS y PostgreSQL mediante `postgresql+psycopg://`; SQLite se rechaza en producción.

## Pruebas

Las pruebas SQLite cubren hashing, TOTP, migraciones, catálogo, inventario, compras, ventas, membresías y autorización multiempresa. La integración PostgreSQL usa un esquema temporal para migraciones, MFA, recuperación, auditoría, operaciones concurrentes de inventario, pagos a proveedores y reservas de pedidos de venta, y luego elimina ese esquema. Solo ejecútala con una base de datos descartable:

```powershell
$env:TEST_DATABASE_URL = "postgresql+psycopg://usuario:clave@localhost:5432/erp_test"
pytest backend/tests -q
```

Sin `TEST_DATABASE_URL`, la suite PostgreSQL se omite. No se debe usar una base con información real.

Documentación OpenAPI: `http://127.0.0.1:8000/docs`. API versionada bajo `/api/v1`.

Para PostgreSQL, establece `DATABASE_URL` en `.env`, por ejemplo `postgresql+psycopg://usuario:clave@localhost:5432/erp`. No guardes secretos en Git.

La guía de configuración del entorno, migraciones, administrador inicial, worker SMTP, backups y preparación operativa está en [docs/operations.md](docs/operations.md). Sus procedimientos de despliegue y restauración aún deben validarse en la infraestructura elegida.

## Estructura

```text
backend/app/{api,core,database,modules/{companies,identity,catalog,inventory,purchasing,sales}}
backend/migrations/{env.py,versions/}
backend/tests/
docs/architecture/
```

La API depende de servicios de módulo, que usan SQLAlchemy y esquemas Pydantic. Las rutas no concentran reglas de dominio. Alembic es la autoridad del esquema; la aplicación ya no crea tablas al arrancar. La migración inicial adopta con `checkfirst` el esquema SQLite de prototipo que pudiera existir y crea las tablas faltantes. Haz una copia de seguridad antes de usarla sobre datos importantes.

## Modelo de datos inicial

- `companies`: razón social, identificador fiscal opcional, país, moneda, zona horaria, locale y estado.
- `branches`: empresa propietaria, código único por empresa, nombre y estado. GET/POST/PATCH se limita a la empresa activa; solo admin puede crear o editar y cada cambio genera evento de seguridad.
- Relación 1:N empresa-sucursal, FK con borrado en cascada, marcas de tiempo e índices de consulta por estado.
- `users`: correo único, hash de contraseña, nombre y estado.
- `memberships`: relación usuario-empresa con estado activo y unicidad por pareja.
- `roles`, `permissions`, `role_permissions` y `membership_roles`: RBAC; catálogo base admin, operator, viewer.
- `auth_sessions`: digest del token, expiración y revocación.
- `warehouses`, `inventory_locations`, `inventory_operations`, `inventory_movements`, `inventory_balances`: posiciones multiempresa y movimientos inmutables; el saldo es una proyección reconciliable.
- `suppliers`, `purchase_orders`, `purchase_receipts`, `supplier_returns`, `supplier_invoices`, `supplier_payments`: documentos de compras/cuentas por pagar separados, auditables y vinculados al ledger cuando afectan existencias.
- `customers`, `sales_quotes`, `sales_orders`, `sales_deliveries`, `sales_returns`, `sales_invoices`, `sales_credit_notes`, `sales_payments`: documentos del ciclo comercial; reservas de pedido se derivan de cantidades abiertas y las salidas/entradas del ledger permanecen inmutables.

Las consultas protegidas primero validan sesión activa, pertenencia activa a la empresa indicada y permiso requerido. No se aceptan IDs de empresa como autorización por sí solos. Los roles base globales se inicializan en la migración; cada empresa puede crear roles personalizados y asignarlos a membresías. La API permite revocar acceso por empresa, pero la suspensión global de una cuenta aún es una tarea pendiente.

El esquema incluye identidad, inventario, compras y ventas iniciales. Faltan el ciclo de vida global de usuarios, contabilidad, matching contable y procesos generales; no se debe usar con datos reales.

## Criterios de aceptación del núcleo

1. La aplicación inicia con configuración de entorno y expone salud y OpenAPI.
2. Empresas se crean con validación de servidor y persisten vía ORM.
3. El listado pagina con límites acotados y una ordenación estable.
4. Empresa y sucursal mantienen integridad referencial y código único por empresa.
5. La migración crea/adopta el esquema y RBAC; login/logout, MFA, recuperación, rate limit, acceso por empresa y permisos están aplicados en la API.
6. Hay pruebas locales SQLite para migraciones, autenticación, membresías y reglas de inventario; PostgreSQL se omite si falta `TEST_DATABASE_URL`.
7. Antes de producción deben pasar las pruebas sobre PostgreSQL real y una revisión de seguridad independiente.

## Riesgos y decisiones pendientes

- El primer administrador de cada instalación se crea por CLI local; proteger el acceso operativo a esa máquina y al archivo de entorno.
- Las funciones SMTP, MFA y recuperación necesitan configuración de secretos/servidor; el envío se probó con un adaptador SMTP simulado, no con un proveedor real.
- No hay un servicio escuchando en `127.0.0.1:5432`, `psql` ni Docker en el entorno inspeccionado; la integración PostgreSQL se preparó pero no se ejecutó aquí.
- Faltan revisión independiente de seguridad, política de retención/limpieza para eventos y outbox, configuración de proxy confiable en despliegue, y pruebas operativas de SMTP/restauración de respaldos antes de comercialización.
- Configurar PostgreSQL, secretos, HTTPS, límites de solicitudes, monitoreo y respaldos/restauración.
- Validar obligaciones fiscales, laborales, contables y de privacidad en México con especialistas; no hay CFDI ni nómina implementados.
- No hay frontend ERP completo ni despliegue Docker en esta etapa.
