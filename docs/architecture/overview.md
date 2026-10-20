# Arquitectura inicial

## Capas y dependencias

```text
Cliente HTTP → API (FastAPI) → casos de uso del módulo → SQLAlchemy → PostgreSQL
                                      ↓
                            esquemas Pydantic
```

Los módulos empresariales se ubican en `backend/app/modules/<módulo>` y no deben importar rutas API. `core` contiene configuración compartida; `database` administra sesiones y metadatos; `api` solo traduce HTTP a casos de uso. Integraciones, auditoría y reportes se añadirán como módulos separados cuando sus primeros flujos lo requieran.

## Módulos y dependencias previstas

- Plataforma: empresas/sucursales → usuarios, membresías, roles/permisos → auditoría/configuración.
- Operación: catálogo de productos → inventario; compras y ventas integran inventario.
- Finanzas consume eventos/documentos de compras, ventas e inventario con asientos balanceados y correcciones trazables.
- RR. HH., fabricación, mantenimiento, proyectos y reportes se incorporarán incrementalmente.

## Modelo entidad-relación inicial

```mermaid
erDiagram
  COMPANIES ||--o{ BRANCHES : owns
  COMPANIES ||--o{ MEMBERSHIPS : grants
  COMPANIES ||--o{ CATALOG_CATEGORIES : groups
  COMPANIES ||--o{ PRODUCTS : owns
  CATALOG_CATEGORIES o|--o{ PRODUCTS : classifies
  USERS ||--o{ MEMBERSHIPS : joins
  USERS ||--o{ AUTH_SESSIONS : opens
  MEMBERSHIPS }o--o{ ROLES : assigned
  ROLES }o--o{ PERMISSIONS : grants
  COMPANIES {
    int id PK
    string legal_name
    string tax_id
    string country_code
    string currency_code
    string timezone
    string locale
    boolean is_active
    datetime created_at
    datetime updated_at
  }
  BRANCHES {
    int id PK
    int company_id FK
    string code
    string name
    boolean is_active
    datetime created_at
    datetime updated_at
  }
  USERS {
    int id PK
    string email UK
    string password_hash
    boolean is_active
    boolean mfa_enabled
    string mfa_secret_encrypted
  }
  MEMBERSHIPS {
    int id PK
    int user_id FK
    int company_id FK
    boolean is_active
  }
  ROLES {
    int id PK
    int company_id FK
    string name
  }
  PERMISSIONS {
    int id PK
    string code UK
  }
  AUTH_SESSIONS {
    int id PK
    int user_id FK
    string token_digest UK
    datetime expires_at
    datetime revoked_at
  }
  USERS ||--o{ SECURITY_EVENTS : generates
  USERS ||--o{ MFA_RECOVERY_CODES : owns
  USERS ||--o{ PASSWORD_RECOVERY_TOKENS : requests
  USERS {
    boolean mfa_enabled
    string mfa_secret_encrypted
  }
  SECURITY_EVENTS {
    int id PK
    int user_id FK
    int company_id FK
    string event_type
    string source_ip
    json details
    datetime created_at
  }
  MFA_RECOVERY_CODES {
    int id PK
    int user_id FK
    string code_digest
    datetime consumed_at
  }
  PASSWORD_RECOVERY_TOKENS {
    int id PK
    int user_id FK
    string token_digest UK
    datetime expires_at
    datetime consumed_at
  }
  CATALOG_CATEGORIES {
    int id PK
    int company_id FK
    string name
    boolean is_active
  }
  PRODUCTS {
    int id PK
    int company_id FK
    int category_id FK
    string sku
    string kind
    string unit_code
    decimal price
    decimal tax_rate
    boolean is_active
  }
```

Una sucursal tiene un código único dentro de su empresa. El acceso de una persona requiere una membresía activa por empresa; los roles de esa membresía agregan permisos. Las claves TOTP se cifran con Fernet; códigos de recuperación y tokens de restablecimiento se persisten en forma digestada. El outbox conserva el token cifrado hasta entrega/expiración. La auditoría rechaza `UPDATE` y `DELETE` mediante triggers, y requiere un proceso futuro de retención compatible con esa política. Un token de sesión aleatorio se entrega al cliente, pero solo su SHA-256 se persiste; la contraseña se guarda como PBKDF2-HMAC-SHA256 salteado.

El catálogo actual es multiempresa, con categorías, productos y servicios, SKU único por empresa y precios/impuestos decimales validados. No guarda saldos de inventario: existencias deberán calcularse a partir de movimientos trazables. `unit_code` aún no tiene un catálogo de unidades de medida y `tax_rate` es solo un porcentaje configurable, no una determinación fiscal.
