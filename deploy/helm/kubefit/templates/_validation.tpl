{{- define "kubefit.validateValues" -}}
{{- if gt (len .Values.rbac.targetNamespaces) 0 }}
  {{- if not .Values.rbac.create }}
    {{- fail "rbac.create must be true when rbac.targetNamespaces is not empty" }}
  {{- end }}
  {{- if not .Values.serviceAccount.automountToken }}
    {{- fail "serviceAccount.automountToken must be true when observation RBAC is enabled" }}
  {{- end }}
  {{- if and (not .Values.serviceAccount.create) (not .Values.serviceAccount.name) }}
    {{- fail "serviceAccount.name is required when observation RBAC uses an existing account" }}
  {{- end }}
{{- end }}
{{- end }}
