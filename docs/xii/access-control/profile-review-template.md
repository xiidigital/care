---
title: Plantilla para revisión de perfil
document: profile-review-template
version: 0.1.0
status: Plantilla
---

# Perfil: NOMBRE_DEL_PERFIL

## 1. Resumen

- **Estado:** Pendiente de revisión
- **Fecha de revisión:** AAAA-MM-DD
- **Entorno:**
- **Usuario de prueba:**
- **Responsabilidad asignada:**
- **Designación asignada:**

Describir en una frase el propósito operativo del perfil.

## 2. Comportamiento esperado

### Puede

- Pendiente.

### No puede

- Pendiente.

## 3. Comportamiento observado

| Área o acción | Visible | Backend permite | Resultado esperado | Estado |
| --- | --- | --- | --- | --- |
| Ejemplo | Sí/No | Sí/No/No probado | Lectura/Edición/Oculto | Pendiente |

## 4. Hallazgos

### HALLAZGO-ID: Título

- **Severidad:** Crítico/Alto/Medio/Bajo
- **Estado:** Abierto/Corregido/Aceptado
- **Frontend:**
- **Backend:**
- **Datos:**
- **Riesgo:**
- **Causa:**

## 5. Requisitos de corrección

### Frontend (`care_fe`)

1. Pendiente o no aplica con justificación.

### Backend (`care`)

1. Pendiente o protección existente con evidencia y prueba de regresión.

### Datos y migraciones (`care`)

1. Pendiente o no aplica con justificación.

## 6. Criterios de aceptación

- [ ] El perfil sólo ve las acciones previstas.
- [ ] Las llamadas directas sin permiso reciben `403 Forbidden`.
- [ ] Una operación rechazada no modifica datos.
- [ ] No es posible asignar un rol con más privilegios.
- [ ] Existen pruebas automatizadas del frontend y del backend.
- [ ] Los datos iniciales respetan los contextos de los roles.
- [ ] Se aplicaron cambios en ambos repositorios cuando el hallazgo lo requería.
- [ ] Si un repositorio no requirió cambios, la razón está documentada.

## 7. Evidencia y referencias

### Backend (`care`)

- Añadir rutas y líneas relevantes.

### Frontend (`care_fe`)

- Añadir rutas y líneas relevantes.

### Evidencia manual

- Añadir capturas, usuario y pasos de reproducción sin incluir credenciales.

## 8. Decisiones pendientes

- Pendiente.

## 9. Cierre del hallazgo

- **Cambios en `care_fe`:**
- **Cambios en `care`:**
- **Pruebas ejecutadas en `care_fe`:**
- **Pruebas ejecutadas en `care`:**
- **Razón de cualquier cambio no aplicable:**
