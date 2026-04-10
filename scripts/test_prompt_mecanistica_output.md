# Informe de Farmacovigilancia — Análisis Mecanístico

## 1. Evaluación Mecanística

### Plausibilidad

**Moderada**

La mucositis es una reacción adversa documentada y consistente con el mecanismo de acción de metotrexato. El fármaco inhibe enzimas clave para la síntesis de nucleótidos (dihidrofolato reductasa, timidilato sintasa, AICART y amido fosforribosiltransferasa), lo que impide la división celular. Las células de la mucosa oral presentan alta tasa de proliferación y renovación, por lo que son particularmente vulnerables a esta inhibición. La cadena causal fármaco → inhibición de síntesis de nucleótidos → alteración de renovación de mucosa oral está respaldada por el mecanismo de acción documentado. Sin embargo, los datos aportados no contienen evidencia molecular estructurada específica que vincule las vías de Reactome identificadas con el desarrollo específico de mucositis.

### Cadena mecanística

Metotrexato → Inhibición de dihidrofolato reductasa (DHFR) → Reducción de tetrahidrofolato → Limitación de fragmentos de un carbono para síntesis de purinas y conversión de desoxiuridilato a timidilato → Inhibición de síntesis de ADN → Alteración de la división celular en tejidos de alta proliferación → Mucositis

**Nota:** La conexión específica entre la inhibición documentada de DHFR y el daño en mucosa oral es biológicamente consistente con el mecanismo citotóxico general, pero no está explícitamente documentada en los datos moleculares estructurados proporcionados como una vía directa mucosa-específica.

### Rutas biológicas implicadas

Las vías moleculares identificadas en Reactome relacionadas con metotrexato son:

1. **DHFR dimer binds DHFR inhibitors** (R-HSA-9709109)
   - Relación con la RAM: **Directa**
   - Representa la interacción molecular primaria del metotrexato con su diana principal.

2. **SLC19A1 transports 5-methyl-THF from extracellular region to cytosol** (R-HSA-200652)
   - Relación con la RAM: **Indirecta**
   - Relacionado con el transporte de folatos reducidos; SLC19A1 también transporta metotrexato, aunque esta función específica no está explícitamente descrita en los datos.

3. **Mismatch repair (MMR) directed by MSH2:MSH6 (MutSalpha)** (R-HSA-5358565)
   - Relación con la RAM: **No evidente**
   - Vía de reparación de errores de apareamiento del ADN; no hay conexión documentada con mucositis en los datos proporcionados.

4. **Mismatch repair (MMR) directed by MSH2:MSH3 (MutSbeta)** (R-HSA-5358606)
   - Relación con la RAM: **No evidente**
   - Similar al anterior, sin relación documentada con la RAM específica.

5. **MSH2:MSH3 binds insertion/deletion loop of 2 bases or more** (R-HSA-5358513)
   - Relación con la RAM: **No evidente**

6. **ICMT methylates S-Farn RAS proteins** (R-HSA-9647977)
   - Relación con la RAM: **No evidente**

7. **Cysmethynil binds ICMT:Zn2+** (R-HSA-9656775)
   - Relación con la RAM: **No evidente**

**Interpretación:** Solo la vía de inhibición de DHFR muestra relación directa y documentada con el mecanismo citotóxico que podría explicar la mucositis. Las vías de reparación de ADN (MMR) y las relacionadas con proteínas RAS carecen de conexión evidente con esta RAM específica en los datos proporcionados.

## 4. Rutas de No Seguridad

### Diana o mecanismo primario

Inhibición de dihidrofolato reductasa (DHFR), timidilato sintasa, AICART y amido fosforribosiltransferasa, resultando en bloqueo de la síntesis de nucleótidos y división celular.

### Rutas de no seguridad

**On-target:**

La mucositis representa una toxicidad directamente derivada del mecanismo terapéutico de metotrexato. La inhibición de DHFR y otras enzimas de síntesis de nucleótidos afecta no solo a células tumorales o hiperproliferativas patológicas, sino también a tejidos sanos con alta tasa de renovación celular, como la mucosa oral. Esta toxicidad es una extensión previsible del efecto farmacológico principal.

La conversión de metotrexato a metotrexato poliglutamato, que potencia la inhibición de AICART y la acumulación de ATP y adenosina extracelular, contribuye al efecto antiinflamatorio en artritis reumatoide, pero este mismo mecanismo no está documentado en los datos como protector o causante de mucositis.

**Off-target:**

No disponible — los datos no documentan mecanismos adversos independientes de la inhibición de DHFR y enzimas relacionadas con la síntesis de nucleótidos.

### Interacciones farmacológicas relevantes

| Interacción | Tipo | Efecto esperado | Relación con mucositis |
|-------------|------|-----------------|------------------------|
| Antibióticos orales (incluyendo neomicina) | Farmacocinética | Aumento de concentraciones plasmáticas de metotrexato | Relevante — mayor exposición puede aumentar toxicidad en mucosas |
| Fármacos antifolato (dapsona, pemetrexed, pirimetamina, sulfonamidas) | Farmacodinámica | Potenciación del efecto antifolato | Relevante — efecto aditivo sobre inhibición de síntesis de nucleótidos |
| Antibióticos penicilínicos o sulfonamidas (oral/IV) | Farmacocinética | Aumento de concentraciones plasmáticas de metotrexato | Relevante — mayor exposición puede aumentar toxicidad en mucosas |
| AINEs y aspirina | Farmacocinética | Aumento de concentraciones plasmáticas de metotrexato; competición por proteínas plasmáticas | Relevante — mayor exposición puede aumentar toxicidad en mucosas |
| Productos hepatotóxicos | Farmacocinética/Farmacodinámica | Aumento de riesgo de reacciones adversas hepatoespecíficas | Indirecta — no específicamente relacionado con mucositis |
| Fármacos altamente unidos a proteínas (anticoagulantes orales, fenitoína, salicilatos, sulfonamidas, sulfonilureas, tetraciclinas) | Farmacocinética | Desplazamiento de proteínas plasmáticas; aumento de fracción libre de metotrexato | Relevante — mayor fracción libre puede aumentar toxicidad |
| Inhibidores de la bomba de protones | Farmacocinética | Aumento de concentraciones plasmáticas de metotrexato | Relevante — mayor exposición puede aumentar toxicidad en mucosas |
| Ácidos débiles (salicilatos) | Farmacocinética | Aumento de concentraciones plasmáticas de metotrexato | Relevante — mayor exposición puede aumentar toxicidad en mucosas |
| Productos nefrotóxicos | Farmacocinética | Reducción de eliminación renal de metotrexato | Relevante — acumulación aumenta riesgo de toxicidad sistémica incluyendo mucositis |
| Probenecid