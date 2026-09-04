{{/*
Expand the name of the chart.
*/}}
{{- define "gw.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
We truncate at 63 chars because some Kubernetes name fields are limited to this (by the DNS naming spec).
If release name contains chart name it will be used as a full name.
*/}}
{{- define "gw.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Create chart name and version as used by the chart label.
*/}}
{{- define "gw.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "gw.cm.labels" -}}
helm.sh/chart: {{ include "gw.chart" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Common labels - agg
*/}}
{{- define "gw.agg.labels" -}}
helm.sh/chart: {{ include "gw.chart" . }}
{{ include "gw.agg.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels - agg
*/}}
{{- define "gw.agg.selectorLabels" -}}
app.kubernetes.io/name: {{ include "gw.name" . }}-agg
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Common labels - sg
*/}}
{{- define "gw.sg.labels" -}}
helm.sh/chart: {{ include "gw.chart" . }}
{{ include "gw.sg.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels - sg
*/}}
{{- define "gw.sg.selectorLabels" -}}
app.kubernetes.io/name: {{ include "gw.name" . }}-sg
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Common labels - rc
*/}}
{{- define "gw.rc.labels" -}}
helm.sh/chart: {{ include "gw.chart" . }}
{{ include "gw.rc.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels - rc
*/}}
{{- define "gw.rc.selectorLabels" -}}
app.kubernetes.io/name: {{ include "gw.name" . }}-rc
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}
