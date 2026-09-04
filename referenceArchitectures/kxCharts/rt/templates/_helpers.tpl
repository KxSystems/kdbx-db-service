{{/*
Chart resources prefix.
*/}}
{{- define "rt.resourcePrefix" -}}
{{- "rt-" }}
{{- end }}

{{/*
RT Stream name.
*/}}
{{- define "rt.streamName" -}}
{{ .Release.Name | trimPrefix ( include "rt.resourcePrefix" . ) }}
{{- end }}

{{/*
Expand the name of the chart.
*/}}
{{- define "rt.name" -}}
{{- .Chart.Name | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
We truncate at 63 chars because some Kubernetes name fields are limited to this (by the DNS naming spec).
If release name contains chart name it will be used as a full name.
*/}}
{{- define "rt.fullname" -}}
{{- $prefix:= include "rt.resourcePrefix" . -}}
{{- $releaseName:= .Release.Name -}}
{{- if ( hasPrefix $prefix $releaseName | not ) -}}
  {{- $releaseName = printf "%s%s" $prefix $releaseName -}}
{{- end -}}

{{- $releaseName | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create chart name and version as used by the chart label.
*/}}
{{- define "rt.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "rt.labels" -}}
helm.sh/chart: {{ include "rt.chart" . }}
{{ include "rt.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "rt.selectorLabels" -}}
app.kubernetes.io/name: {{ include "rt.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
insights.kx.com/serviceName: {{ include "rt.fullname" . }}
insights_kx_com_app: {{ .Release.Name }}
insights_kx_com_pubTopic: {{ include "rt.streamName" . }}
{{- end }}

{{/*
Create the name of the service account to use
*/}}
{{- define "rt.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "rt.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}
