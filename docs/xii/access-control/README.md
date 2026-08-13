---
title: Revisión de perfiles y responsabilidades
document: access-control-review
version: 0.1.0
status: En progreso
last_reviewed: 2026-08-11
---

# Revisión de perfiles y responsabilidades

## 1. Propósito

Este directorio contiene la especificación funcional y de seguridad de los
perfiles de CARE desplegados para XII.

El objetivo es revisar, perfil por perfil:

- qué información puede consultar;
- qué operaciones puede ejecutar;
- qué controles presenta el frontend;
- qué operaciones autoriza realmente el backend;
- si la interfaz y la autorización son consistentes;
- qué cambios y pruebas se requieren antes de producción.

La autorización del backend es la fuente de verdad. Ocultar un control en el
frontend mejora la experiencia y reduce errores, pero no sustituye una
validación del servidor.

## 2. Regla de trabajo entre repositorios

La documentación de esta auditoría se mantiene centralizada en el repositorio
backend `care`, dentro de `docs/xii/access-control`, para evitar especificaciones
duplicadas o divergentes.

Cada revisión e implementación DEBE considerar los dos repositorios:

- `care_fe`: visibilidad, navegación, estados de sólo lectura, mensajes de
  error y pruebas de interfaz;
- `care`: permisos, autorización, validación de contexto, integridad de datos,
  fixtures, migraciones y pruebas de API.

Cuando un hallazgo sea relevante para el backend, la solución no se considerará
completa hasta que se haya realizado una de estas acciones en `care`:

1. implementar la validación o corrección necesaria y añadir sus pruebas; o
2. documentar con evidencia que la protección ya existe y añadir o fortalecer
   la prueba de regresión correspondiente.

Una corrección exclusivamente visual en `care_fe` NO se considera una solución
de seguridad. Del mismo modo, una protección correcta en `care` no justifica
mostrar acciones inutilizables o engañosas en el frontend.

## 3. Estado de la revisión

| Perfil | Estado | Hallazgo principal | Especificación |
| --- | --- | --- | --- |
| Volunteer | Revisado; correcciones y decisiones pendientes | Controles de responsabilidades sin permiso, ámbito operativo ausente en fixtures y Developer Mode sin bandera global de build | [Volunteer](roles/volunteer.md) |
| Doctor | Pendiente | Por revisar | Pendiente |
| Nurse | Pendiente | Por revisar | Pendiente |
| Staff | Línea base documentada; revisión visual pendiente | Rol operativo amplio con acceso clínico, financiero, logístico y de configuración | [Staff](roles/staff.md) |
| Administrator | Pendiente | Por revisar | Pendiente |
| Facility Admin | Pendiente | Por revisar | Pendiente |
| Pharmacist | Pendiente | Por revisar | Pendiente |
| Role Organization Member | Pendiente | Por revisar | Pendiente |
| Role Organization Manager | Pendiente | Por revisar | Pendiente |
| Role Organization Admin | Pendiente | Por revisar | Pendiente |

## 4. Convenciones del modelo

CARE distingue dos conceptos que no deben confundirse:

1. **Responsabilidad u organización de rol:** agrupación como `Volunteer`,
   `Doctor` o `Nurse`.
2. **Designación dentro de una organización de rol:** `Member`, `Manager` o
   `Admin`.

Los roles del sistema también pueden estar limitados por contexto:

- `FACILITY`;
- `GOVT_ORG`;
- `ROLE_ORG`.

Toda asignación debe respetar el contexto declarado por el rol. Una pantalla
que permita seleccionar un rol no prueba que el usuario esté autorizado para
asignarlo.

## 5. Flujo para revisar un perfil

1. Iniciar sesión con un usuario que tenga únicamente el perfil bajo revisión.
2. Registrar las secciones visibles del menú y del perfil.
3. Abrir cada acción visible y comprobar si es lectura o edición.
4. Verificar la respuesta del backend para cada operación sensible.
5. Confirmar que una respuesta denegada no haya producido cambios.
6. Comparar los resultados con los permisos declarados en `care/security`.
7. Documentar diferencias entre frontend, backend y datos iniciales.
8. Añadir pruebas de autorización y de visibilidad para cada hallazgo.
9. Aplicar cambios en `care_fe`, `care` o ambos según el impacto documentado.
10. No cerrar el hallazgo hasta verificar conjuntamente interfaz y API.

Para perfiles nuevos, copiar [la plantilla de revisión](profile-review-template.md)
y actualizar esta tabla.

## 6. Criterio de severidad

- **Crítico:** el backend permite elevar privilegios o acceder a datos sin
  autorización.
- **Alto:** el backend permite una modificación indebida o exposición de datos
  sensibles.
- **Medio:** el frontend presenta acciones no autorizadas que el backend
  rechaza, o los datos no respetan el modelo previsto.
- **Bajo:** texto, nomenclatura o comportamiento visual confuso sin impacto de
  autorización.
