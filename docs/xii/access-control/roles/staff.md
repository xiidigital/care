---
title: Perfil Staff
document: access-control-staff
version: 0.1.0
status: Línea base documentada; revisión visual pendiente
last_reviewed: 2026-08-11
---

# Perfil Staff

## 1. Resumen

- **Perfil:** Staff
- **Cuenta inicial de referencia:** `care-staff`
- **Permisos declarados:** 63
- **Propósito inferido:** operación administrativa cotidiana de una institución
- **Revisión de interfaz:** Pendiente
- **Revisión de autorización mediante API:** Pendiente

Staff contiene todos los permisos de Volunteer y añade operaciones sobre
pacientes, agenda, facturación, infraestructura física, dispositivos,
medicamentos y suministros.

No forma una jerarquía estricta con Doctor o Nurse. Staff tiene menos permisos
totales, pero posee algunas capacidades administrativas que los perfiles
clínicos no tienen.

## 2. Ámbito de los datos iniciales

Los fixtures crean al usuario dentro de la responsabilidad `Staff` y también lo
asocian al departamento `Administration` de la institución de prueba.

Por ello, a diferencia de `care-volunteer`, se espera que `care-staff` tenga una
institución disponible en su dashboard. Los recursos efectivos siguen
dependiendo de la organización, departamento, ubicación, encuentro o paciente
al que se encuentre vinculado.

La asociación adicional a la responsabilidad `Staff` conserva la misma
inconsistencia de contexto identificada para Volunteer: el rol operativo Staff
declara contextos de institución y organización gubernamental, pero los
fixtures también lo utilizan como designación dentro de una organización de
rol.

## 3. Capacidades declaradas

### Pacientes y formularios

Staff puede:

- listar, crear y actualizar pacientes;
- consultar datos clínicos del paciente;
- consultar respuestas de cuestionarios;
- responder cuestionarios generales, de paciente y de encuentro;
- leer y crear/actualizar plantillas de respuesta;
- leer plantillas y generar reportes a partir de ellas.

### Agenda y atención administrativa

Staff puede:

- consultar y crear/actualizar agendas;
- listar, crear/actualizar y reprogramar reservas o citas;
- listar y emitir tokens;
- listar y crear categorías de tokens.

### Facturación y pagos

Staff puede:

- consultar cuentas;
- crear y consultar cargos;
- consultar y modificar facturas;
- consultar y modificar conciliaciones de pago.

### Institución, ubicaciones y dispositivos

Staff puede:

- consultar y actualizar la institución;
- consultar organizaciones y usuarios;
- consultar organizaciones internas de la institución y sus usuarios;
- listar, crear y actualizar ubicaciones;
- asociar organizaciones con ubicaciones;
- listar y administrar dispositivos;
- asociar dispositivos con encuentros.

### Medicamentos, laboratorio y suministros

Staff puede:

- consultar y registrar dispensación de medicamentos;
- consultar especímenes y definiciones de especímenes;
- consultar reportes diagnósticos;
- consultar inventario, productos y conocimiento de productos;
- crear y consultar solicitudes de suministro;
- consultar entregas de suministros;
- consultar solicitudes de servicio.

### Catálogos operativos

Staff puede consultar servicios de salud, categorías de recursos, definiciones
de actividades, definiciones de observaciones, definiciones de cargos,
configuración de identificadores de pacientes y etiquetas. Puede aplicar
etiquetas a recursos.

## 4. Límites declarados

Staff no puede:

- crear usuarios ni administrar asignaciones de roles;
- crear o eliminar instituciones;
- crear, eliminar o administrar organizaciones internas de la institución;
- crear, listar, leer o modificar encuentros;
- leer o escribir datos clínicos propios del encuentro;
- crear o modificar reportes diagnósticos;
- crear o modificar solicitudes de servicio;
- registrar entregas de suministros;
- crear o modificar productos, inventario o definiciones clínicas;
- crear, actualizar o eliminar cuentas;
- destruir facturas o conciliaciones;
- administrar facturas bloqueadas;
- diseñar, archivar o administrar el acceso de cuestionarios;
- crear usuarios de servicio.

## 5. Inconsistencias y riesgos que deben revisarse

### STF-001: acceso clínico sin acceso normal a encuentros

Staff posee `can_view_clinical_data` y puede enviar cuestionarios de encuentro,
pero no posee los permisos para listar, leer, crear o actualizar encuentros.
Debe comprobarse qué información clínica puede ver realmente y desde qué ruta.

### STF-002: operaciones sensibles concentradas en un rol genérico

El mismo perfil puede modificar pacientes, facturas, conciliaciones, agendas,
ubicaciones, dispositivos y dispensaciones de medicamentos. Debe confirmarse si
“Staff” representa realmente todas estas funciones en XII o si deben separarse
responsabilidades como recepción, facturación, farmacia y operaciones.

### STF-003: escritura de plantillas de respuesta

Staff no puede diseñar cuestionarios, pero sí crear o actualizar plantillas de
respuesta. Debe verificarse si esta capacidad es necesaria para su operación.

### STF-004: actualización de la institución

Staff puede actualizar datos de la institución sin ser Facility Admin. Se debe
identificar exactamente qué campos expone el frontend y qué campos acepta el
backend.

## 6. Guía para la revisión visual

Durante la sesión con `care-staff`, revisar como mínimo:

1. instituciones y departamentos visibles;
2. listado, creación y edición de pacientes;
3. información clínica visible sin abrir un encuentro;
4. agenda, reservas y tokens;
5. cuentas, cargos, facturas y conciliaciones;
6. inventario, suministros y entregas;
7. dispensación de medicamentos y laboratorio;
8. ubicaciones y dispositivos;
9. configuración visible de la institución;
10. perfil propio, responsabilidades y Developer Mode.

Para cada control visible se debe probar si el backend permite realmente la
operación y confirmar que una respuesta denegada no modifica datos.

## 7. Criterios preliminares de aceptación

- [ ] La navegación coincide con el ámbito institucional asignado.
- [ ] Staff no puede crear usuarios ni modificar roles.
- [ ] Staff no puede administrar encuentros clínicos.
- [ ] El acceso a información clínica está definido y limitado explícitamente.
- [ ] Las operaciones financieras visibles corresponden al proceso de XII.
- [ ] La dispensación de medicamentos está aprobada para este perfil o se
  elimina.
- [ ] La edición de institución está limitada a campos aprobados.
- [ ] Las acciones rechazadas por el backend no aparecen como utilizables.
- [ ] Existen pruebas de frontend y backend para las decisiones aprobadas.

## 8. Referencias

### Backend (`care`)

- `care/security/roles/role.py`
- `care/security/permissions/`
- `care/security/authorization/`
- `care/fixtures/scripts/default_fixtures.py`

### Frontend (`care_fe`)

- `src/pages/UserDashboard.tsx`
- `src/common/Permissions.tsx`
- `src/pages/Facility/`
- `src/pages/Organization/`
- `src/components/Users/`
