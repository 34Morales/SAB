# Guía operativa inicial

Esta guía describe los procedimientos que expone el código actual. No sustituye la configuración del proveedor, una revisión de seguridad ni un ensayo en infraestructura real. No se ha validado todavía con PostgreSQL, SMTP, proxy inverso o restauración desplegados.

## Entorno y secretos

Configura el entorno mediante variables del gestor de secretos de la plataforma, no en Git. `ENVIRONMENT=production` requiere `DATABASE_URL` con `postgresql+psycopg://`, `AUTH_THROTTLE_PEPPER` aleatorio de al menos 32 caracteres, `MFA_ENCRYPTION_KEY` Fernet válido, `PUBLIC_BASE_URL` HTTPS y `SMTP_HOST`/`SMTP_FROM`. Si el SMTP autentica, establece usuario y contraseña juntos. `CORS_ORIGINS` debe ser un arreglo JSON de orígenes HTTPS exactos, por ejemplo `["https://erp.example.com"]`; no uses `*`.

Genera los dos secretos criptográficos una vez por instalación, guárdalos en un gestor de secretos y comparte los mismos valores entre instancias:

```powershell
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Haz una copia segura de esos valores. Perder la clave Fernet impide descifrar MFA y tokens pendientes del outbox; cambiar el pepper rompe las claves de throttling existentes. Planifica rotación explícita antes de cambiar cualquiera. En producción configura TLS para PostgreSQL y verifica el requisito de certificado/CA del proveedor; la aplicación no fuerza por sí sola `sslmode`.

## Despliegue y migraciones

1. Usa un release revisado y respaldado; verifica configuración y conectividad a una base PostgreSQL vacía o de staging.
2. Ejecuta `python -m pip install -e "./backend"` en un entorno compatible con Python 3.12 o superior.
3. Antes de actualizar una base existente, toma respaldo y revisa el destino/versión Alembic con el operador de base.
4. Aplica el esquema explícitamente una sola vez por despliegue: `alembic -c backend/alembic.ini upgrade head`.
5. Crea el administrador inicial desde una sesión segura de consola: `python -m backend.app.cli`. No incluyas la contraseña en argumentos, logs ni archivos del repositorio.
6. Inicia la aplicación ASGI con el servidor de proceso elegido por el despliegue, detrás de HTTPS, y valida `/api/v1/health` y `/openapi.json`.
7. Verifica login, MFA, cambio de empresa, permisos, recepción/entrega de inventario y recuperación mediante una cuenta de prueba antes de abrir tráfico.

La aplicación no migra el esquema al arrancar ni crea automáticamente al primer administrador. La ruta `/health` informa que el proceso HTTP responde; no es un chequeo de preparación de PostgreSQL ni de correo.

## Recuperación de contraseña por correo

El endpoint de recuperación agrega mensajes a un outbox cifrado en base de datos. Programa un worker con acceso a la misma base y secretos:

```powershell
python -m backend.app.modules.identity.mailer
```

Una ejecución procesa hasta 20 mensajes exitosos; se detiene cuando la cola está vacía o el primer envío falla. El mensaje fallido queda para reintento y un scheduler debe invocarlo periódicamente. Frecuencia, concurrencia, alertas y retención deben definirse en el despliegue. Revisa rebotes/entrega con el proveedor. No registres tokens, enlaces, códigos MFA ni contraseñas.

## Copias y restauración

- Define RPO/RTO, retención, cifrado en reposo, ubicación separada y acceso mínimo con el operador de datos.
- Para PostgreSQL, usa las herramientas de backup administradas por tu proveedor o una exportación como `pg_dump` con credenciales de entorno; conserva aparte claves/secretos necesarios para descifrar datos.
- Ejecuta restauraciones periódicas en una instancia aislada, valida esquema con Alembic, conteos/reconciliación de inventario y acceso de prueba. Nunca ensayes restauración sobre la base productiva.
- La aplicación no configura backups, retención, réplicas ni restauración automática. Hasta probar un restore completo, el procedimiento de recuperación no se considera validado.

## Supervisión y salida a producción

Centraliza logs sin secretos; mide errores HTTP, fallos de login/throttling, retraso del outbox, conexiones PostgreSQL, errores de migración y desajustes de ledger/reservas. Configura límites de solicitudes y proxy confiable en la infraestructura; el backend no establece una política de rate limit general para todas las rutas ni configura cabeceras de proxy.

Antes de producción aún requieren evidencia: integración PostgreSQL y concurrencia, SMTP real con TLS, pruebas tras el proxy, backup/restore, observabilidad, revisión independiente de seguridad y revisión fiscal/privacidad. No uses CFDI ni reportes contables como validados por esta API.
