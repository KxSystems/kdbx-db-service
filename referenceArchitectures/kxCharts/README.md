# kxCharts

Collection of base charts for DB Service reference architectures

1. [DB Service](./db-service)

## Core Principles

1. Minimal runtime configuration
1. Self documenting `values.yaml`
1. Follow Helm best practices
1. Open source configuration
1. Include common Kubernetes fields
1. Consistent KX License configuration

### Minimal runtime configuration

The default `values.yaml` configuration for all charts should allow for the chart to be successfully deployed. It is expected a minimal user defined `myvalues.yaml` may be necessary to allow secrets and license information to be applied, specifically the `imagePullSecrets` which contains the repository access details.

### Self Documenting values.yaml

The chart should be written with a self documenting `values.yaml`. All end users should be able to run `helm show values .` within the base chart directory and review the available configuration settings which can be modified through their own `myvalues.yaml`

### Follow Helm best practices

Helm best practices are documented [here](https://helm.sh/docs/chart_best_practices/). Charts implemented should follow these best practices.

Charts should also minimize complex helpers in use to ensure those not familiar with the chart can pick it up via reviewing the configuration.

Any helpers in use should be clearly documented within the chart; and how the helper can be used and manipulated with the `values.yaml`

### Open source configuration

The goal of this repo is to open source all the deployment configuration of the DB Service charts. It should not include any reference to configuration not readily available and open source.

### Include common Kubernetes fields

For consistency and completeness it is expected that all charts include common Kubernetes configuration fields unless there is distinct reason to not include them.

NOTE: they do not necessarily have to have defaults, but should allow users to insert configurations to allow them to apply to the deployed charts

- service.annotations
- podAnnotations
- podLabels
- resources
- podSecurityContext
- securityContext
- nodeSelector
- tolerations
- affinity

### Consistent KX License configuration

As it is necessary to have a kx license available to run DB Service q processes we should be consistent on how charts incorporate this license to avoid unnecessary configuration.

This should follow

- Having a secret with the name `kx-license` and the B64 data field `license`
    _Contact KX to get a license_

    ```bash
    LIC_FILE=./kc.lic
    kubectl create secret generic kx-license --from-file=license=$LIC_FILE -n $NAMESPACE
    ```

- Any q component should include a volume and volumeMount to mount the license appropriately.

```bash
...
          volumeMounts:
            - name: kx-license
              mountPath: /opt/kx/lic
              readOnly: true
      volumes:
        - name: kx-license
          secret:
            defaultMode: 420
            items:
            - key: license
              # path: k4.lic
              path: {{ .Values.kxLicenseName }}
            optional: false
            secretName: kx-license
...
```

- An entry in the `values.yaml` consisting of

```yaml
...
# -- You must set your license name. Default is `"kc.lic"`.
# Available types are:
#  - `"kc.lic"`
#  - `"k4.lic"`
#  - `"kx.lic"`
kxLicenseName: "kc.lic"
...
```
