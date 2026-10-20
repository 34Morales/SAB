# Controles de identidad implementados

## Credenciales y sesiones

- Contraseñas con PBKDF2-HMAC-SHA256, sal aleatoria de 16 bytes y 600,000 iteraciones.
- Sesiones bearer opacas de 8 horas; el token completo solo se entrega al inicio de sesión y se persiste su SHA-256. Cierre de sesión y restablecimiento de contraseña revocan las sesiones correspondientes.
- Respuestas con token, secreto MFA y códigos de recuperación llevan `Cache-Control: no-store`.
- La autenticación usa el encabezado `Authorization: Bearer`; no hay cookies de sesión. CORS solo se activa con una lista configurada de orígenes y no permite credenciales de navegador. En producción los orígenes configurados deben ser HTTPS exactos, sin comodín.

## Límite de intentos

Los contadores se guardan en `login_throttles` y se actualizan atómicamente mediante `INSERT .. ON CONFLICT DO UPDATE` en PostgreSQL y SQLite. Hay una ventana fija de 15 minutos, bloqueo de 15 minutos al quinto fallo por correo/IP, y un límite adicional de 30 por IP. Se aplica a login y a los intentos fallidos al confirmar o desactivar MFA. Los identificadores se derivan con HMAC de `AUTH_THROTTLE_PEPPER`; se necesita el mismo secreto en todas las instancias de una instalación. No se confía en cabeceras IP reenviadas por el cliente.

## MFA y recuperación

TOTP sigue RFC 6238 con HMAC-SHA1, 6 dígitos, periodo de 30 segundos y tolerancia de un paso a cada lado. El paso aceptado se persiste para bloquear replay concurrente. El secreto TOTP se cifra con Fernet (`MFA_ENCRYPTION_KEY`). Diez códigos aleatorios de alta entropía se entregan una sola vez y se consumen atómicamente.

El restablecimiento genera un token aleatorio con vencimiento de 20 minutos, persiste su digest y cifra la copia temporal del token para el outbox. Tokens previos se invalidan al emitir uno nuevo. Un trabajador SMTP envía con STARTTLS o TLS implícito, reintenta hasta cinco veces con espera exponencial y borra destinatario/token del outbox cuando entrega o agota intentos. La página de restablecimiento no carga recursos externos y utiliza CSP con nonce, `no-store` y `no-referrer`.

## Auditoría

`security_events` registra tipo de evento, sujeto pseudonimizado con HMAC, empresa, IP directa y detalles acotados sin secretos. Los triggers SQL rechazan `UPDATE` y `DELETE` tanto en SQLite como en PostgreSQL. La API lista eventos solo para la empresa activa y requiere el permiso `security:audit:read`.

La gestión de identidad ofrece `GET /api/v1/identity/memberships` con `identity:read`, `PATCH /api/v1/identity/memberships/{id}` con `identity:manage` para cambiar roles o activar/desactivar acceso, y `POST/PATCH /api/v1/identity/roles` para gestionar roles personalizados. Solo se pueden asignar roles globales o del mismo tenant; los roles globales de sistema no se editan desde la API. Los cambios se auditan y se mantiene al menos un administrador de identidad activo; la fila de empresa se bloquea en PostgreSQL para serializar cambios simultáneos. La revocación de membresía se aplica al siguiente control de acceso aunque el token siga vigente.

## Límites conocidos

- Todavía no hay MFA obligatorio por política, SSO, notificaciones de cambios de seguridad ni administración de sesiones activas.
- El modo producción rechaza SQLite y requiere PostgreSQL con el driver psycopg.
- El trabajador SMTP debe desplegarse como proceso programado. La integración se probó con un servidor SMTP simulado, no con un proveedor real.
- La política de retención y exportación de eventos aún no está definida. No se deben borrar filas de auditoría desde la aplicación.
- IP de origen depende del proxy directo observado por ASGI; al desplegar tras balanceadores, configura proxies confiables y no aceptes `X-Forwarded-For` arbitrario.
- CSRF basado en cookies no aplica al mecanismo actual de bearer token. Aun así, un cliente web debe proteger el token contra XSS, servir todo por HTTPS y limitar CORS a sus orígenes reales.
- Un administrador con privilegios elevados en PostgreSQL puede omitir triggers. Para mayor garantía, la aplicación debe usar un rol de base de datos con privilegio mínimo y la auditoría debe replicarse fuera del mismo dominio administrativo.
- No se ejecutó la suite PostgreSQL en este entorno. `backend/tests/test_postgres_identity_integration.py` crea un esquema temporal, migra, prueba flujos concurrentes y elimina ese esquema cuando `TEST_DATABASE_URL` apunta a una base descartable.
