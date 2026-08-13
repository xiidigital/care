---
title: Perfil Volunteer y administración de responsabilidades
document: access-control-volunteer
version: 0.1.0
status: Hallazgo confirmado; corrección pendiente
last_reviewed: 2026-08-11
---

# Perfil Volunteer

## 1. Resumen

- **Perfil revisado:** Volunteer
- **Área:** Perfil de usuario > Responsibility Assignments
- **Severidad:** Media
- **Impacto confirmado:** interfaz inconsistente y datos iniciales ambiguos
- **Escalación de privilegios observada:** No
- **Protección verificada en código:** autorización del backend
- **Prueba manual contra el despliegue:** Pendiente

Un Volunteer debe poder consultar las responsabilidades que tiene asignadas,
pero no debe poder administrarlas. En la interfaz actual, el usuario puede abrir
`Manage Assignments`, ver la acción para eliminar su asignación y seleccionar
las designaciones `Member`, `Manager` y `Admin`.

El backend valida estas operaciones y debe responder `403 Forbidden` para un
Volunteer sin permisos adicionales. Por lo tanto, el hallazgo no demuestra una
escalación efectiva de privilegios, pero sí es un defecto que debe corregirse
antes de producción.

## 2. Comportamiento esperado

### El Volunteer puede

- ver que pertenece a la responsabilidad `Volunteer`;
- ver su designación vigente;
- consultar esta información en modo de sólo lectura.

### El Volunteer no puede

- abrir un editor de responsabilidades;
- agregar una responsabilidad;
- eliminar su responsabilidad actual;
- cambiar su designación;
- seleccionar o asignarse `Member`, `Manager` o `Admin`;
- enviar solicitudes de creación, actualización o eliminación de membresías.

Los controles administrativos sólo deben presentarse cuando el usuario actual
posea el permiso efectivo para administrar usuarios en la organización de rol
correspondiente. Ser propietario de la ficha de usuario no concede ese permiso.

## 3. Comportamiento observado

| Elemento | Observado para Volunteer | Esperado |
| --- | --- | --- |
| Resumen de responsabilidades | Visible | Visible |
| Responsabilidad `Volunteer` | Visible | Visible |
| Botón `Manage Assignments` | Visible | Oculto |
| Acción de eliminar | Visible | Oculta |
| Formulario `Add Assignment` | Visible | Oculto |
| Selector `Member/Manager/Admin` | Visible | Oculto |
| Creación o eliminación en backend | No probada manualmente; código y pruebas generales indican `403` | Debe responder `403` |

## 4. Hallazgo VOL-001: controles administrativos sin autorización

### Causa en el frontend

`UserSummaryTab` muestra el resumen de organizaciones de rol para todos los
usuarios y no transmite un estado de sólo lectura ni un permiso de gestión a
`RoleOrgAccessSummary`.

`RoleOrgAccessSummary` renderiza incondicionalmente:

- el botón `Manage Assignments`;
- el botón de eliminación;
- el selector de organizaciones accesibles;
- el selector de roles con contexto `ROLE_ORG`;
- el botón para agregar una asignación.

El cálculo `canEditUser = authUser.is_superuser || isOwnAccount` tampoco debe
reutilizarse como autorización para administrar responsabilidades. Un usuario
puede editar aspectos permitidos de su propio perfil sin poder modificar sus
roles.

### Protección en el backend

El backend exige `can_manage_organization_users` o, para organizaciones
conectadas, `can_manage_connected_role_organizations`. Volunteer no recibe
ninguno de estos permisos.

Además, el backend comprueba que los permisos del rol solicitado sean un
subconjunto de los permisos del actor. Esta validación impide asignar un rol
superior incluso a un usuario que sí pueda administrar membresías limitadas.

Las operaciones de creación, actualización y eliminación pasan por
`can_manage_organization_users_obj` y generan `PermissionDenied` cuando la
validación falla.

### Riesgo

- El usuario interpreta que puede elevar sus privilegios.
- Se generan errores evitables y mensajes genéricos al usar controles que nunca
  deberían estar habilitados.
- La interfaz puede ocultar futuras regresiones si se asume que el backend
  siempre rechazará la operación.
- Se exponen nombres y descripciones de designaciones administrativas sin una
  necesidad funcional para Volunteer.

## 5. Hallazgo VOL-002: inconsistencia entre responsabilidad y designación

Los datos iniciales crean una organización de rol llamada `Volunteer` y asignan
al usuario `care-volunteer` el rol del sistema `Volunteer` dentro de esa
organización.

Sin embargo:

- el rol `Volunteer` declara los contextos `FACILITY` y `GOVT_ORG`;
- las designaciones válidas mostradas para una organización de rol son
  `Member`, `Manager` y `Admin`, cuyo contexto es `ROLE_ORG`.

Esto produce una ficha donde `Volunteer` aparece tanto como responsabilidad
como designación, mientras el editor ofrece un conjunto diferente de
designaciones.

Antes de migrar datos debe decidirse explícitamente si el modelo deseado es:

1. responsabilidad `Volunteer` + designación `Member`; o
2. rol operativo `Volunteer` asociado directamente al contexto de institución u
   organización gubernamental, sin representarlo como una organización de rol.

No se debe aplicar una migración destructiva hasta confirmar cuál de estos
modelos representa el proceso real de XII.

## 6. Requisitos de corrección

### Frontend (`care_fe`)

1. Añadir un estado explícito de sólo lectura a
   `RoleOrgAccessSummary`, o separar el componente de consulta del editor.
2. Calcular la autorización de gestión con permisos efectivos de la
   organización, no con `isOwnAccount`.
3. Ocultar `Manage Assignments`, eliminar y agregar cuando no haya autorización.
4. No solicitar la lista de roles administrativos si el componente está en modo
   de sólo lectura.
5. Mostrar un mensaje específico si una operación administrativa recibe `403`,
   como defensa adicional ante permisos desactualizados.

### Backend (`care`)

1. Conservar todas las comprobaciones de autorización actuales.
2. Añadir una prueba explícita con el rol del sistema Volunteer intentando:
   - asignarse `Member`;
   - asignarse `Manager`;
   - asignarse `Admin`;
   - eliminar su propia membresía.
3. Verificar que cada operación responda `403` y no modifique la base de datos.
4. Validar que el contexto del rol sea compatible con el tipo de organización
   durante la creación o actualización de una membresía.

### Datos iniciales y migración

1. Revisar la creación de `role_orgs` en los fixtures.
2. Evitar asignar roles `FACILITY`/`GOVT_ORG` dentro de organizaciones
   `ROLE_ORG`, salvo que exista una decisión arquitectónica documentada.
3. Preparar primero un reporte de asociaciones incompatibles en los datos
   desplegados.
4. Definir una migración sólo después de aprobar el modelo objetivo.

## 7. Criterios de aceptación

- [ ] Volunteer ve sus responsabilidades y designaciones en modo de sólo
  lectura.
- [ ] Volunteer no ve `Manage Assignments`.
- [ ] Volunteer no ve acciones para agregar, cambiar o eliminar asignaciones.
- [ ] El frontend no carga el selector de roles administrativos para Volunteer.
- [ ] Una petición directa de creación como Volunteer recibe `403 Forbidden`.
- [ ] Una petición directa de actualización como Volunteer recibe
  `403 Forbidden`.
- [ ] Una petición directa de eliminación como Volunteer recibe
  `403 Forbidden`.
- [ ] Ninguna petición rechazada modifica membresías.
- [ ] Un administrador autorizado conserva las acciones de gestión.
- [ ] No es posible asignar un rol cuyos permisos superen los del actor.
- [ ] Las nuevas membresías respetan el contexto del rol.
- [ ] Los fixtures ya no generan asociaciones incompatibles.
- [ ] Existen pruebas automatizadas de interfaz y backend.

## 8. Escenarios mínimos de prueba

### Frontend

1. Iniciar sesión como Volunteer y abrir el perfil propio.
2. Confirmar que el resumen `Responsibility Assignments` sea visible.
3. Confirmar que no existan botones de gestión ni eliminación.
4. Iniciar sesión como administrador autorizado y confirmar que el editor siga
   disponible.
5. Simular una respuesta `403` durante gestión y verificar un mensaje claro sin
   cambios optimistas persistentes.

### Backend

| Actor | Operación | Resultado |
| --- | --- | --- |
| Volunteer | Listar sus responsabilidades | Permitido |
| Volunteer | Agregar `Member` | `403` |
| Volunteer | Agregar `Manager` | `403` |
| Volunteer | Agregar `Admin` | `403` |
| Volunteer | Eliminar su responsabilidad | `403` |
| Role Organization Admin autorizado | Administrar membresía permitida | Permitido |
| Actor autorizado | Asignar un rol superior a sus permisos | `403` |

## 9. Referencias de implementación

### Frontend (`care_fe`)

- `src/components/Users/UserSummary.tsx`: renderiza el resumen de
  responsabilidades sin proporcionar autorización de gestión.
- `src/components/Users/UserRoleOrganizationAccess.tsx`: renderiza y ejecuta
  incondicionalmente las acciones de agregar y eliminar.
- `src/components/Common/RoleSelect.tsx`: lista roles filtrados por contexto;
  para este editor utiliza `ROLE_ORG`.
- `src/components/Common/AccessibleRoleOrgSelect.tsx`: lista organizaciones de
  rol accesibles, lo cual no implica permiso para modificar membresías.

### Backend (`care`)

- `care/security/roles/role.py`: contextos de Volunteer, Member, Manager y
  Admin.
- `care/security/permissions/organization.py`: permisos para consultar y
  administrar usuarios de organizaciones.
- `care/security/authorization/organization.py`: comprobación del permiso y de
  que el rol solicitado no supere al actor.
- `care/emr/api/viewsets/organization.py`: autorización de creación,
  actualización y eliminación de membresías.
- `care/emr/tests/test_organization_api.py`: pruebas generales de operaciones
  permitidas, operaciones sin permiso y asignación de roles superiores.
- `care/fixtures/scripts/default_fixtures.py`: creación inicial de la
  organización y el usuario Volunteer.

## 10. Instrucciones para implementación posterior

Antes de modificar código:

1. confirmar el modelo objetivo descrito en VOL-002;
2. revisar cambios locales existentes en ambos repositorios;
3. implementar pruebas que reproduzcan VOL-001;
4. realizar el cambio mínimo compatible con upstream;
5. ejecutar pruebas del frontend y del backend relacionadas con usuarios,
   organizaciones y roles;
6. no considerar resuelto VOL-001 únicamente por ocultar los controles en
   `care_fe`: añadir en `care` las pruebas explícitas de Volunteer descritas en
   esta especificación;
7. si se decide corregir VOL-002, implementar en `care` la validación de contexto
   y el tratamiento aprobado para fixtures o datos existentes;
8. actualizar el estado de este documento y el índice de revisión.

## 11. Pendiente transversal detectado durante la revisión

La sección `Software update & cache` es válida para Volunteer porque opera sólo
sobre la copia local del frontend y no concede permisos. Durante su revisión se
identificó un pendiente de despliegue que afecta a todos los perfiles:

- verificar que Firebase sirva `/build-meta.json` con una política que evite
  respuestas obsoletas del CDN;
- comprobar los encabezados reales en staging y producción;
- validar la detección de actualización entre dos despliegues consecutivos.

El seguimiento detallado está registrado como **M1** en
`docs/xii/architecture/inventory/unresolved-items.md`. Actualmente corresponde
al repositorio `care_fe`; no se ha identificado un cambio necesario en el
backend `care`.

## 12. Hallazgo VOL-003: permisos nominales sin ámbito operativo

### Comportamiento observado

El usuario inicial `care-volunteer` sólo muestra la responsabilidad `Volunteer`
y su listado de usuarios. No dispone de una institución en el dashboard y no
presenta áreas de cuentas, facturación, inventario o trabajo clínico.

Esto no contradice por sí solo la tabla de permisos. En CARE, un permiso define
qué operación admite un rol, pero la autorización también exige que el usuario
esté relacionado con la organización, institución, departamento, encuentro o
paciente que contiene el recurso.

### Causa en los datos iniciales

`care/fixtures/scripts/default_fixtures.py` crea `care-volunteer` dentro de una
organización de rol llamada `Volunteer`. Los pacientes de prueba se crean bajo
la organización gubernamental y los encuentros bajo la institución y el
departamento de medicina general.

Después, el fixture sólo añade `Facility Admin`, `Nurse` y `Staff` al
departamento de administración. Volunteer no recibe una asociación con la
institución, un departamento, la organización gubernamental que contiene los
pacientes ni pacientes concretos.

En consecuencia:

- los permisos de lectura financiera y operativa no producen navegación de
  institución;
- el acceso a pacientes no encuentra un ámbito común con los pacientes de
  prueba;
- los cuestionarios de paciente sólo pueden utilizarse después de obtener
  acceso legítimo al paciente correspondiente;
- “apoyo operativo” describe una responsabilidad de negocio, no una pantalla
  independiente de CARE.

### Decisión requerida

XII debe definir el ámbito real de Volunteer antes de modificar fixtures o
permisos. Las alternativas que deben evaluarse son:

1. acceso limitado a pacientes asignados directamente;
2. acceso a pacientes de una organización gubernamental concreta;
3. pertenencia a una institución o departamento determinado;
4. una combinación explícita de los anteriores.

El principio recomendado es conceder el ámbito mínimo necesario. La existencia
actual de permisos de lectura para cuentas, facturas, conciliaciones, reportes
diagnósticos e inventario no significa que todos los voluntarios deban recibir
acceso a una institución que los haga efectivos.

### Criterios de aceptación

- [ ] Existe una definición operativa aprobada del ámbito de Volunteer en XII.
- [ ] Los fixtures representan esa definición sin asociaciones de contexto
  incompatibles.
- [ ] La navegación muestra únicamente módulos utilizables en el ámbito
  asignado.
- [ ] Los listados no aparecen vacíos debido a una asociación artificial con la
  organización de responsabilidad equivocada.
- [ ] Se revisan expresamente los permisos financieros, diagnósticos y de
  inventario antes de habilitar acceso institucional.

## 13. Hallazgo VOL-004: Developer Mode sin bandera global de build

### Comportamiento actual

La sección `Developer Mode` se muestra a cualquier usuario cuando consulta su
propia ficha, sin importar si es Volunteer, personal clínico o administrador.
El interruptor sólo activa un banner visual de advertencia; no concede permisos
ni habilita operaciones adicionales.

El valor se guarda en `localStorage` bajo la clave `care:developer_mode`:

- afecta únicamente al navegador, perfil del navegador y origen actuales;
- no cambia una preferencia global del servidor;
- no se sincroniza con otros dispositivos;
- no está ligado al usuario, por lo que otro usuario que inicie sesión en el
  mismo navegador y dominio hereda el valor;
- permanece después de cerrar sesión;
- también puede activarse visitando una URL con `?debug=true`.

El componente no comprueba por sí mismo si el frontend realmente corresponde a
producción. Si el valor está activo, el banner se presenta también en otros
entornos.

### Decisión de producto

Developer Mode no se controla por perfil ni por permisos de usuario. Su
disponibilidad debe ser una decisión global del build del frontend.

Cada compilación desplegada en Firebase debe declarar si incluye o no esta
función para todos sus usuarios:

- **deshabilitada:** ningún usuario ve la sección, el banner nunca se renderiza
  y `?debug=true` no produce ningún efecto;
- **habilitada:** todos los usuarios del mismo build pueden ver y utilizar la
  función. El interruptor puede continuar siendo una preferencia local del
  navegador.

La misma bandera debe controlar conjuntamente la sección del perfil,
`ProductionWarningBanner` y la activación mediante query string. No deben
existir caminos alternos que ignoren la configuración global.

### Requisitos

1. Añadir una bandera booleana de configuración del frontend, suministrada al
   proceso de build de Firebase. El nombre definitivo debe seguir las
   convenciones existentes de `care.config.ts`; una opción compatible sería
   `REACT_ENABLE_DEVELOPER_MODE`.
2. Definir el valor por ambiente en CI/CD y no mediante una configuración del
   backend.
3. Usar `false` como valor predeterminado seguro cuando la variable esté
   ausente o sea inválida.
4. Cuando la bandera sea `false`, no renderizar `DeveloperModeSection` ni
   `ProductionWarningBanner` y no procesar `?debug=true`.
5. Cuando la bandera sea `true`, conservar una experiencia consistente para
   todos los usuarios del build, sin filtrar por Volunteer, Doctor, Nurse,
   Staff o Administrator.
6. Mantener documentado que el estado del interruptor es local al navegador y
   al origen, mientras la disponibilidad de la función es global para el build.
7. Mostrar el banner sólo cuando el entorno detectado sea producción, o cambiar
   su texto si se desea una advertencia manual independiente del entorno.

### Criterios de aceptación

- [ ] Un build con la bandera deshabilitada no muestra Developer Mode a ningún
  usuario.
- [ ] Un build con la bandera deshabilitada ignora `?debug=true`.
- [ ] Un build con la bandera habilitada ofrece la función a todos sus usuarios.
- [ ] La configuración se define y verifica en el pipeline de Firebase.
- [ ] Se documenta que la disponibilidad es global por build y el estado del
  interruptor es local por navegador/origen.
- [ ] El banner y su texto corresponden al entorno real.
