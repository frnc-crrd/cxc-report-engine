#!/usr/bin/env bash
# Pipeline diario CxC — ejecutado por systemd o manualmente.
# Reintentos: hasta 3 veces con 30 minutos de espera entre intentos.
# Al terminar (exito o agotados reintentos), apaga el equipo via rtcwake.

set -euo pipefail

PROYECTO="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="$PROYECTO/.venv/bin/python"
LOG_DIR="$PROYECTO/logs"
LOG_FILE="$LOG_DIR/pipeline_$(date +%Y%m%d).log"
MAX_INTENTOS=3
ESPERA_SEG=$((30 * 60))

mkdir -p "$LOG_DIR"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"
}

# Verificar dia de la semana (1=Lunes … 5=Viernes)
dia=$(date +%u)
if [ "$dia" -gt 5 ]; then
    log "Dia no laboral ($dia) — pipeline omitido."
    exit 0
fi

# Levantar contenedor PostgreSQL si no esta corriendo
if command -v podman &>/dev/null; then
    log "Levantando contenedor cobrador_db..."
    podman compose -f "$PROYECTO/compose.yml" up -d cobrador_db || true
    sleep 5
fi

# Ciclo de reintentos
EXITO=false
for intento in $(seq 1 $MAX_INTENTOS); do
    log "Intento $intento/$MAX_INTENTOS..."
    if "$PYTHON" "$PROYECTO/main.py" >> "$LOG_FILE" 2>&1; then
        log "Pipeline completado con exito en el intento $intento."
        EXITO=true
        break
    fi
    log "Intento $intento fallo."
    if [ "$intento" -lt "$MAX_INTENTOS" ]; then
        log "Esperando $((ESPERA_SEG / 60)) minutos antes del siguiente intento..."
        sleep "$ESPERA_SEG"
    fi
done

if [ "$EXITO" = false ]; then
    log "Pipeline fallo tras $MAX_INTENTOS intentos. Revisa $LOG_FILE."
fi

# Programar el siguiente encendido (proximo dia laboral a las 06:50)
# 10 min antes del timer systemd (07:00) para que el equipo este listo al arrancar
siguiente_encendido() {
    hoy=$(date +%u)   # 1=Lun … 7=Dom
    if   [ "$hoy" -eq 5 ]; then dias_hasta=3  # Vie → Lun
    elif [ "$hoy" -eq 6 ]; then dias_hasta=2  # Sab → Lun
    else                         dias_hasta=1  # cualquier otro → dia siguiente
    fi
    date -d "+${dias_hasta} day 06:50" +%s 2>/dev/null || \
    python3 -c "
import datetime
d = datetime.date.today() + datetime.timedelta(days=${dias_hasta})
t = datetime.datetime.combine(d, datetime.time(6, 50))
print(int(t.timestamp()))
"
}

EPOCH=$(siguiente_encendido)
log "Programando encendido para $(date -d "@$EPOCH" '+%Y-%m-%d %H:%M') via rtcwake..."
sudo rtcwake -m off -t "$EPOCH"
