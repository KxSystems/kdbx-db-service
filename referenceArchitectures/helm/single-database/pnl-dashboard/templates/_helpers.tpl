{{/* Object names are prefixed with the DB Service release they belong to. */}}
{{- define "pnl-dashboard.prefix" -}}
{{- printf "%s-pnl-dashboard" .Values.dbService.release | trunc 50 | trimSuffix "-" }}
{{- end }}

{{- define "pnl-dashboard.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
app.kubernetes.io/name: {{ .Chart.Name }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/part-of: pnl-dashboard
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}
