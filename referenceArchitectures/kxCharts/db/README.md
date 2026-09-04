# db Chart

## Description

This chart deploys the DB Service database components to allow a client to ingest and persist data. This chart deploys independently with the sole function of persisting data. Access to the data is typically via the [DB Service gateway chart](../gw) which due to different scaling characteristics is typically deployed independently.

![db chart](../../img/db-chart.png)

## Running

### Prerequisites

1. A working Kubernetes cluster with appropriate access to deploy applications
1. A Distributed storage solution offering RWM access level. (Refer to the [Kubernetes](https://kubernetes.io/docs/concepts/storage/persistent-volumes/#access-modes  documentation for more details. ))
1. `helm` command installed on your local machine
1. Authentication details to Kx image repositories

  ```bash
  KX_USER=....
  KX_PASS=....
  KX_REGISTRY="portal.dl.kx.com"
  NAMESPACE="db-service"
  ```

1. `imagePullSecrets` setup on your cluster

  ```bash
  kubectl create secret docker-registry kx-pull-secret --docker-username=$KX_USER --docker-password=$KX_PASS --docker-server=$KX_REGISTRY -n $NAMESPACE

  ```

1. A license secret

  _Contact KX to get a license_

  ```bash
  LIC_FILE=./kc.lic
  kubectl create secret generic kx-license --from-file=license=$LIC_FILE -n $NAMESPACE
  ```

1. A deployment specific values file (`myvalues.yaml`) with configurations relative to your deployment. Available configurations are documented in the chart. This can be displayed by running

  ```bash
  # Run from kxCharts/db directory
  helm show values .
  ```

  A minimum `myvalues.yaml` configuration would contain

  ```yaml
  imagePullSecrets:
  - name: kx-pull-secret

  # -- You must set your license name. Default is `"kc.lic"`.
  # Available types are:
  #  - `"kc.lic"`
  #  - `"k4.lic"`
  #  - `"kx.lic"`
  kxLicenseName: "kc.lic"

  db:
    # Intraday persistent storage configuration
    idb:
      # Defines the storageClassNames for all the persistent data storage in the DB Service database
      # This should map to Kubernetes storageClassNames with accessMode of `RWM`.
      # More information can be found here: https://kubernetes.io/docs/concepts/storage/storage-classes/
      storageClassName: "sharedfiles"
      # Size of the Intraday persistent storage
      size: "2Gi"
    # HDB Tier 1 persistent storage configuration
    hdbt1:
      # Defines the storageClassNames for all the persistent data storage in the DB Service database
      # This should map to Kubernetes storageClassNames with accessMode of `RWM`.
      # More information can be found here: https://kubernetes.io/docs/concepts/storage/storage-classes/
      storageClassName: "sharedfiles"
      # Size of the HDB Tier 1 persistent storage
      size: "2Gi"
    # Additional HDB tiers can be added to migrate data to slower storage as it ages.
    # See the DB Service documentation for more information.

  dbsDa:
    # This should map to the resource coordinator svc deployed with the gw chart
    # Typically naming convention is '{Chart.name}-gw-rc', but if the {Chart.name} is 'gw' it will remove the duplication
    rcAddr: "gw-rc:5040"
  ```

### Deploying

```bash
# Run from '.../kxCharts/db' directory
RELEASENAME=db # Unique name for this deployment
VALUESFILE=myvalues.yaml
helm install $RELEASENAME . -f $VALUESFILE -n $NAMESPACE
```

### Upgrading/updating config

Upgrading and updating configuration are executed using `helm upgrade`. This will deploy any changes made to the charts or configuration since the last deploy and automatically redeploy the latest to the application

```bash
helm upgrade $RELEASENAME . -f $VALUESFILE -n $NAMESPACE
```

## Custom Deployment Assembly

The default [assembly.yaml](assembly.yaml) contains deployment wiring for labels, RT stream, mounts, and SM tiers. Table schemas should be created through the DB Service table API after deployment.

To change the deployment wiring, provide a custom assembly file:

1. Create your own assembly file `myassembly.yaml` in the base directory of the `db` chart.
1. Update your `myvalues.yaml` to use that assembly file.

    ```
    ...
    assembly:
      filename: myassembly.yaml
    ...
    ```

1. Deploy as normal detailed [here](#deploying).

## Configuration Options

### Local Configurations

Local values configuration for `db`.

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `assembly.filename` | `string` | <code>"assembly.yaml"</code> | This defines the assembly file to be loaded into the DB Service database.<br>It should be on the base directory of the chart folder under `db`.<br>See the DB Service documentation for more information. |
| `db.hdbt1.size` | `string` | <code>""</code> | Size of the HDB Tier 1 persistent storage. |
| `db.hdbt1.storageClassName` | `string` | <code>""</code> | Defines the storageClassNames for all the persistent data storage in the DB Service database.<br>This should map to Kubernetes storageClassNames with accessMode of `RWM`.<br>More information can be found [here](https://kubernetes.io/docs/concepts/storage/storage-classes/). |
| `db.idb.size` | `string` | <code>""</code> | Size of the Intraday persistent storage. |
| `db.idb.storageClassName` | `string` | <code>""</code> | Defines the storageClassNames for all the persistent data storage in the DB Service database.<br>This should map to Kubernetes storageClassNames with accessMode of `RWM`.<br>More information can be found [here](https://kubernetes.io/docs/concepts/storage/storage-classes/). |
| `fullnameOverride` | `string` | <code>""</code> | This sets the full name of the DB Service database deployment.<br>Override the default fully qualified app name.<br>By default resources are named using `<.Release.Name>-<.Chart.Name>`.<br>Used when generating resource names. |
| `image` | `object` | <code>{}</code> | Default image repository and pull settings for DB Service database chart components.<br>Refer to [Images](https://kubernetes.io/docs/concepts/containers/images/). |
| `image.pullPolicy` | `string` | <code>"IfNotPresent"</code> | Image pull policy.<br>Refer to [Image Pull Policy](https://kubernetes.io/docs/concepts/containers/images/#image-pull-policy). |
| `image.repository` | `string` | <code>"portal.dl.kx.com/"</code> | Image repository. |
| `imagePullSecrets` | `list` | <code>[]</code> | Image pull secrets to be applied to all pods within the chart.<br>For pulling an image from a private repository.<br>More information can be found [here](https://kubernetes.io/docs/tasks/configure-pod-container/pull-image-private-registry/). |
| `kxLicenseName` | `string` | <code>"kc.lic"</code> | You must set your license name.<br>Available types are:  - `"kc.lic"`  - `"k4.lic"`  - `"kx.lic"` |
| `dbsDa.affinity` | `object` | <code>{}</code> | This is for setting Kubernetes Affinity to a Pod.<br>Refer to [Pod Affinity](https://kubernetes.io/docs/concepts/scheduling-eviction/assign-pod-node/#affinity-and-anti-affinity). |
| `dbsDa.allowedSbxApis` | `string` | <code>".query.q,.query.sql"</code> | Sets the available free string queries available for the DA.<br> Each has security and performance implications.<br>See the DB Service documentation for more information. |
| `dbsDa.args` | `list` | <code>[]</code> | Can provide any valid `q` runtime argument here.<br>Full details [here](https://code.kx.com/q/basics/cmdline/).<br>**NOTE** the `-p` argument will be overridden by the `KXI_PORT` env variable. |
| `dbsDa.customCodeEntry` | `string` | <code>"udas.q"</code> | Entrypoint for custom code written under `customcode`.<br>Create your custom code and UDAs under here and it will be loaded into the DA under `/opt/kx/packages/{customCodeEntry}.q`.<br>See the DB Service documentation for more information. |
| `dbsDa.envs` | `list` | <code>[<br/>&nbsp;&nbsp;{<br/>&nbsp;&nbsp;&nbsp;&nbsp;"name": "KXI_LOG_FORMAT",<br/>&nbsp;&nbsp;&nbsp;&nbsp;"value": "text"<br/>&nbsp;&nbsp;},<br/>&nbsp;&nbsp;{<br/>&nbsp;&nbsp;&nbsp;&nbsp;"name": "KXI_LOG_LEVELS",<br/>&nbsp;&nbsp;&nbsp;&nbsp;"value": "default:debug"<br/>&nbsp;&nbsp;}<br/>]</code> | Default common environment variables for the DA.<br>Additional ENVs can be added and these overridden in custom values files.<br>If overriding `envs`, ensure you include all the default values. |
| `dbsDa.image` | `object` | <code>{}</code> | This sets overriding container image information.<br>Use if you wish to target specific versions of the DA.<br>More information can be found [here](https://kubernetes.io/docs/concepts/containers/images/). |
| `dbsDa.image.tag` | `string` | <code>""</code> | Image tag. |
| `dbsDa.nodeSelector` | `object` | <code>{}</code> | Allows adding node selector constraints to a Pod.<br>This constrains the pods to run only on nodes that match the specified labels.<br>Dictionary of key-value pairs.<br>Refer to [NodeSelector](https://kubernetes.io/docs/concepts/scheduling-eviction/assign-pod-node/#nodeselector). |
| `dbsDa.podAnnotations` | `object` | <code>{}</code> | This is for setting Kubernetes Annotations to a Pod.<br>Dictionary of key-value pairs.<br>Refer to [Object Annotations](https://kubernetes.io/docs/concepts/overview/working-with-objects/annotations/). |
| `dbsDa.podLabels` | `object` | <code>{}</code> | This is for setting Kubernetes Labels to a Pod.<br>Dictionary of key-value pairs.<br>Refer to [Object Labels](https://kubernetes.io/docs/concepts/overview/working-with-objects/labels/). |
| `dbsDa.podManagementPolicy` | `string` | <code>"Parallel"</code> | Define how pods are created/deleted.<br>`OrderedReady`, sequential; `Parallel`, all at once.<br>Refer to [Pod Management](https://kubernetes.io/docs/concepts/workloads/controllers/statefulset/#pod-management-policies). |
| `dbsDa.rcAddr` | `string` | <code>""</code> | Defines the name and port of the RC to allow the data to be accessed via queries.<br>This must be set to the appropriate full FQDN RC service and port. |
| `dbsDa.replicaCount` | `int` | <code>1</code> | This sets the number of replicas for the DA.<br>More information can be found [here](https://kubernetes.io/docs/concepts/workloads/controllers/deployment/#replicas-and-scaling). |
| `dbsDa.resources` | `object` | <code>{}</code> | DA Kubernetes resources.<br>Refer to [Container Resources](https://kubernetes.io/docs/concepts/configuration/manage-resources-containers/). |
| `dbsDa.rtLogs` | `object` | <code>{<br/>&nbsp;&nbsp;"size": "2Gi"<br/>}</code> | This defines how much storage should be assigned for rt logs storage in the DA. |
| `dbsDa.secureEnabled` | `bool` | <code>false</code> | Defines whether DB Service database is secured to prevent qsql-based string executions.<br>If enabled only API function queries will be allowed on the DB Service database. |
| `dbsDa.service` | `object` | <code>{}</code> | This is for setting up a service and the port in use by the DA.<br>More information can be found [here](https://kubernetes.io/docs/concepts/services-networking/service/). |
| `dbsDa.service.annotations` | `object` | <code>{}</code> | This sets the service annotations for the DAP.<br>Dictionary of key-value pairs.<br>Refer to [Object Annotations](https://kubernetes.io/docs/concepts/overview/working-with-objects/annotations/). |
| `dbsDa.service.port` | `int` | <code>5040</code> | Set exposed Service Port.<br>Refer to [Service Ports](https://kubernetes.io/docs/concepts/services-networking/service/#field-spec-ports). |
| `dbsDa.serviceClassConfig` | `string` | <code>"dap"</code> | Links the DA to the appropriate service class configuration in your assembly under `elements.dap.instances`.<br>See the DB Service documentation for more information. |
| `dbsDa.tolerations` | `list` | <code>[]</code> | This is for setting Kubernetes Tolerations to a Pod.<br>This allows the pods to be scheduled on nodes with matching taints.<br>Refer to [Taint and Tolerations](https://kubernetes.io/docs/concepts/scheduling-eviction/taint-and-toleration/). |
| `dbsDa.updateStrategy` | `object` | <code>{}</code> | Define how pod updates are applied.<br>`RollingUpdate`, replace gradually; `OnDelete`, replace only on delete.<br>More information can be found [here](https://kubernetes.io/docs/concepts/workloads/controllers/statefulset/#update-strategies). |
| `dbsDa.volumeMounts` | `list` | <code>[]</code> | Allows additional `volumeMounts` to be added to the DA Refer to [Volumes](https://kubernetes.io/docs/concepts/storage/volumes/). |
| `dbsDa.volumes` | `list` | <code>[]</code> | Allows additional `volumes` to be added to the DAP.<br>Refer to [Volumes](https://kubernetes.io/docs/concepts/storage/volumes/). |
| `dbsSidecar.image` | `object` | <code>{}</code> | This sets overriding container image information.<br>Use if you wish to target specific versions of the dbsSidecar.<br>More information can be found [here](https://kubernetes.io/docs/concepts/containers/images/). |
| `dbsSidecar.image.tag` | `string` | <code>"1.18.1"</code> | Image tag. |
| `dbsSm.affinity` | `object` | <code>{}</code> | This is for setting Kubernetes Affinity to a Pod.<br>Refer to [Pod Affinity](https://kubernetes.io/docs/concepts/scheduling-eviction/assign-pod-node/#affinity-and-anti-affinity). |
| `dbsSm.args` | `list` | <code>[]</code> | Can provide any valid `q` runtime argument here.<br>Full details [here](https://code.kx.com/q/basics/cmdline/).<br>**NOTE** the `-p` argument will be overridden by the `KXI_PORT` env variable. |
| `dbsSm.envs` | `list` | <code>[<br/>&nbsp;&nbsp;{<br/>&nbsp;&nbsp;&nbsp;&nbsp;"name": "KXI_LOG_FORMAT",<br/>&nbsp;&nbsp;&nbsp;&nbsp;"value": "text"<br/>&nbsp;&nbsp;},<br/>&nbsp;&nbsp;{<br/>&nbsp;&nbsp;&nbsp;&nbsp;"name": "KXI_LOG_LEVELS",<br/>&nbsp;&nbsp;&nbsp;&nbsp;"value": "default:debug"<br/>&nbsp;&nbsp;}<br/>]</code> | Default common environment variables for the SM.<br>Additional ENVs can be added and these overridden in custom values files.<br>If overriding `envs`, ensure you include all the default values. |
| `dbsSm.image` | `object` | <code>{}</code> | This sets overriding container image information.<br>Use if you wish to target specific versions of the SM.<br>More information can be found [here](https://kubernetes.io/docs/concepts/containers/images/). |
| `dbsSm.image.tag` | `string` | <code>""</code> | Image tag. |
| `dbsSm.nodeSelector` | `object` | <code>{}</code> | Allows adding node selector constraints to a Pod.<br>This constrains the pods to run only on nodes that match the specified labels.<br>Dictionary of key-value pairs.<br>Refer to [NodeSelector](https://kubernetes.io/docs/concepts/scheduling-eviction/assign-pod-node/#nodeselector). |
| `dbsSm.podAnnotations` | `object` | <code>{}</code> | This is for setting Kubernetes Annotations to a Pod.<br>Dictionary of key-value pairs.<br>Refer to [Object Annotations](https://kubernetes.io/docs/concepts/overview/working-with-objects/annotations/). |
| `dbsSm.podLabels` | `object` | <code>{}</code> | This is for setting Kubernetes Labels to a Pod.<br>Dictionary of key-value pairs.<br>Refer to [Object Labels](https://kubernetes.io/docs/concepts/overview/working-with-objects/labels/). |
| `dbsSm.podManagementPolicy` | `string` | <code>"Parallel"</code> | Define how pods are created/deleted.<br>`OrderedReady`, sequential; `Parallel`, all at once.<br>Refer to [Pod Management](https://kubernetes.io/docs/concepts/workloads/controllers/statefulset/#pod-management-policies). |
| `dbsSm.resources` | `object` | <code>{}</code> | Storage Manager Kubernetes resources.<br>Refer to [Container Resources](https://kubernetes.io/docs/concepts/configuration/manage-resources-containers/). |
| `dbsSm.rtLogs` | `object` | <code>{<br/>&nbsp;&nbsp;"size": "2Gi"<br/>}</code> | This defines how much storage should be assigned for rt logs storage in the SM. |
| `dbsSm.service` | `object` | <code>{}</code> | This is for setting up a service and the port in use by the SM.<br>More information can be found [here](https://kubernetes.io/docs/concepts/services-networking/service/). |
| `dbsSm.service.port` | `int` | <code>5040</code> | Set exposed Service Port.<br>Refer to [Service Ports](https://kubernetes.io/docs/concepts/services-networking/service/#field-spec-ports). |
| `dbsSm.tolerations` | `list` | <code>[]</code> | This is for setting Kubernetes Tolerations to a Pod.<br>This allows the pods to be scheduled on nodes with matching taints.<br>Refer to [Taint and Tolerations](https://kubernetes.io/docs/concepts/scheduling-eviction/taint-and-toleration/). |
| `dbsSm.updateStrategy` | `object` | <code>{}</code> | Define how pod updates are applied.<br>`RollingUpdate`, replace gradually; `OnDelete`, replace only on delete.<br>More information can be found [here](https://kubernetes.io/docs/concepts/workloads/controllers/statefulset/#update-strategies). |
| `dbsSm.volumeMounts` | `list` | <code>[]</code> | Allows additional `volumeMounts` to be added to the SM.<br>Refer to [Volumes](https://kubernetes.io/docs/concepts/storage/volumes/). |
| `dbsSm.volumes` | `list` | <code>[]</code> | Allows additional `volumes` to be added to the SM.<br>Refer to [Volumes](https://kubernetes.io/docs/concepts/storage/volumes/). |
| `lateData` | `bool` | <code>false</code> | Defines whether the DB Service database has `lateData` enabled.<br>See the DB Service documentation for more information. |
| `metrics.enabled` | `bool` | <code>true</code> | Enable metric generation. |
| `metrics.handler` | `object` | <code>{}</code> | Enable metric capture for each of the `.z.*` kdb handlers, .e.g { pg: true }.<br>Refer to [dotz](https://code.kx.com/q/ref/dotz/). |
| `nameOverride` | `string` | <code>""</code> | This sets the name of the DB Service database deployment.<br>Overriding Chart name.<br>Used when generating resource names. |
| `podSecurityContext` | `object` | <code>{}</code> | Default Pod Security Context for DB Service database chart components.<br>Refer to [Pod Security Context](https://kubernetes.io/docs/tasks/configure-pod-container/security-context/#set-the-security-context-for-a-pod). |
| `securityContext` | `object` | <code>{}</code> | Default security context for DB Service database chart components.<br>Refer to [Security Context](https://kubernetes.io/docs/tasks/configure-pod-container/security-context/#set-the-security-context-for-a-container). |
| `sidecar.connPortList` | `list` | <code>[<br/>&nbsp;&nbsp;5080,<br/>&nbsp;&nbsp;5081,<br/>&nbsp;&nbsp;5082,<br/>&nbsp;&nbsp;5083<br/>]</code> | List of target ports. |
| `sidecar.enabled` | `bool` | <code>true</code> | Enabled Sidecar metric scraping. |
| `sidecar.frequencySecs` | `int` | <code>10</code> | Frequency of scrapes from target container ports. |
| `stream` | `object` | <code>{<br/>&nbsp;&nbsp;"name": "stream",<br/>&nbsp;&nbsp;"prefix": "rt-"<br/>}</code> | Defines the RT stream name associated with the deployed DB Service database instance.<br>The prefix must match the deployed name of the RT stream, minus the replica number.<br>Associated RT Stream pods 'rt-stream-<REPLICA-NO>' |
