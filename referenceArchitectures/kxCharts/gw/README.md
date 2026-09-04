# gw Chart

## Description

This chart deploys the DB Service gateway components to allow a client to query [DB Service databases](../db). This chart is deployed independently so it can scale separately and route to one or more DB Service database charts.

![gw chart](../../img/gw-chart.png)

## Running on Kubernetes

### Prerequisites

1. A working Kubernetes cluster with appropriate access to deploy applications
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
    # Run from kxCharts/gw directory
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
    ```

### Deploying

```bash
# Run from '.../kxCharts/gw' directory
RELEASENAME=gw # Unique name for this deployment
VALUESFILE=myvalues.yaml
helm install $RELEASENAME . -f $VALUESFILE -n $NAMESPACE
```

### Upgrading/updating config

Upgrading and updating configuration are executed using `helm upgrade`. This will deploy any changes made to the charts or configuration since the last deploy and automatically redeploy the latest to the application

```bash
helm upgrade $RELEASENAME . -f $VALUESFILE -n $NAMESPACE
```

## Configuration Options

### Local Configurations

Local values configuration for `gw`.

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `fullnameOverride` | `string` | <code>""</code> | This sets the full name of the DB ServiceGW deployment.<br>Override the default fully qualified app name.<br>By default resources are named using `<.Release.Name>-<.Chart.Name>`.<br>Used when generating resource names. |
| `image` | `object` | <code>{}</code> | Default image repository and pull settings for DB Service gateway chart components.<br>Refer to [Images](https://kubernetes.io/docs/concepts/containers/images/). |
| `image.pullPolicy` | `string` | <code>"IfNotPresent"</code> | Image pull policy.<br>Refer to [Image Pull Policy](https://kubernetes.io/docs/concepts/containers/images/#image-pull-policy). |
| `image.repository` | `string` | <code>"portal.dl.kx.com/"</code> | Image repository. |
| `imagePullSecrets` | `list` | <code>[]</code> | Image pull secrets to be applied to all pods within the chart.<br>For pulling an image from a private repository.<br>Refer to [Image Pull Secrets](https://kubernetes.io/docs/tasks/configure-pod-container/pull-image-private-registry/). |
| `kxLicenseName` | `string` | <code>"kc.lic"</code> | You must set your license name.<br>Available types are:  - `"kc.lic"`  - `"k4.lic"`  - `"kx.lic"` |
| `dbsAgg.affinity` | `object` | <code>{}</code> | Allows adding affinities to the Aggregator.<br>Refer to [Pod Affinity](https://kubernetes.io/docs/concepts/scheduling-eviction/assign-pod-node/#affinity-and-anti-affinity). |
| `dbsAgg.args` | `list` | <code>[]</code> | Can provide any valid `q` runtime argument here.<br>Full details [here](https://code.kx.com/q/basics/cmdline/).<br>**NOTE** the `-p` argument will be overridden by the `KXI_PORT` env variable. |
| `dbsAgg.customCode` | `object` | <code>{}</code> | Allows for custom code to be loaded into the Aggregator specifically for UDAs typically deployed in a db chart.<br>See the DB Service documentation for more information. |
| `dbsAgg.customCode.configMap` | `string` | <code>""</code> | If `customCode.configMap` is populated there should be a `configMap` deployed by that name and it will be mounted into the Aggregator. |
| `dbsAgg.customCode.entry` | `string` | <code>""</code> | The entrypoint should be the name of the file which is executed to load the custom code. |
| `dbsAgg.envs` | `list` | <code>[<br/>&nbsp;&nbsp;{<br/>&nbsp;&nbsp;&nbsp;&nbsp;"name": "KXI_LOG_FORMAT",<br/>&nbsp;&nbsp;&nbsp;&nbsp;"value": "text"<br/>&nbsp;&nbsp;},<br/>&nbsp;&nbsp;{<br/>&nbsp;&nbsp;&nbsp;&nbsp;"name": "KXI_LOG_LEVELS",<br/>&nbsp;&nbsp;&nbsp;&nbsp;"value": "default:debug"<br/>&nbsp;&nbsp;}<br/>]</code> | Default common environment variables for the Aggregator.<br>Additional ENVs can be added and these overridden in custom values files.<br>If overriding `envs`, ensure you include all the default values. |
| `dbsAgg.image` | `object` | <code>{}</code> | This sets overriding container image information.<br>Use if you wish to target specific versions of the Agg.<br>More information can be found [here](https://kubernetes.io/docs/concepts/containers/images/). |
| `dbsAgg.image.tag` | `string` | <code>""</code> | Image tag. |
| `dbsAgg.nodeSelector` | `object` | <code>{}</code> | Allows adding node selector constraints to the Aggregator.<br>This constrains the pods to run only on nodes that match the specified labels.<br>Dictionary of key-value pairs.<br>Refer to [NodeSelector](https://kubernetes.io/docs/concepts/scheduling-eviction/assign-pod-node/#nodeselector). |
| `dbsAgg.podAnnotations` | `object` | <code>{}</code> | This is for setting Kubernetes Annotations to a Pod.<br>Dictionary of key-value pairs.<br>Refer to [Object Annotations](https://kubernetes.io/docs/concepts/overview/working-with-objects/annotations/). |
| `dbsAgg.podLabels` | `object` | <code>{}</code> | This is for setting Kubernetes Labels to a Pod.<br>Dictionary of key-value pairs.<br>Refer to [Object Labels](https://kubernetes.io/docs/concepts/overview/working-with-objects/labels/). |
| `dbsAgg.replicaCount` | `int` | <code>1</code> | This sets the number of replicas for the Aggregator.<br>More information can be found [here](https://kubernetes.io/docs/concepts/workloads/controllers/deployment/#replicas-and-scaling). |
| `dbsAgg.resources` | `object` | <code>{}</code> | Aggregator Kubernetes resources.<br>Refer to [Container Resources](https://kubernetes.io/docs/concepts/configuration/manage-resources-containers/). |
| `dbsAgg.service` | `object` | <code>{}</code> | This is for setting up an Aggregator service.<br>More information can be found [here](https://kubernetes.io/docs/concepts/services-networking/service/). |
| `dbsAgg.service.annotations` | `object` | <code>{}</code> | This sets the service annotations for the Aggregator.<br>Dictionary of key-value pairs.<br>Refer to [Object Annotations](https://kubernetes.io/docs/concepts/overview/working-with-objects/annotations/). |
| `dbsAgg.service.port` | `int` | <code>5060</code> | Set exposed Service Port.<br>Refer to [Service Ports](https://kubernetes.io/docs/concepts/services-networking/service/#field-spec-ports). |
| `dbsAgg.service.type` | `string` | <code>"ClusterIP"</code> | Sets the Service type.<br>Refer to [Service Types](https://kubernetes.io/docs/concepts/services-networking/service/#publishing-services-service-types). |
| `dbsAgg.tolerations` | `list` | <code>[]</code> | Allows adding tolerations to the Aggregator.<br>This allows the pods to be scheduled on nodes with matching taints.<br>Refer to [Taint and Tolerations](https://kubernetes.io/docs/concepts/scheduling-eviction/taint-and-toleration/). |
| `dbsAgg.volumeMounts` | `list` | <code>[]</code> | Allows additional `volumeMounts` to be added to the Aggregator. |
| `dbsAgg.volumes` | `list` | <code>[]</code> | Allows additional `volumes` to be added to the Aggregator. |
| `dbsRc.affinity` | `object` | <code>{}</code> | Allows adding affinities to the Resource Coordinator.<br>Refer to [Pod Affinity](https://kubernetes.io/docs/concepts/scheduling-eviction/assign-pod-node/#affinity-and-anti-affinity). |
| `dbsRc.allowedSbxApis` | `string` | <code>".query.q,.query.sql"</code> | Sets the available free string queries available for the RC.<br> Each has security and performance implications.<br>See the DB Service documentation for more information. |
| `dbsRc.args` | `list` | <code>[]</code> | Can provide any valid `q` runtime argument here.<br>Full details [here](https://code.kx.com/q/basics/cmdline/).<br>**NOTE** the '-p' argument will be overridden by the `KXI_PORT` env variable. |
| `dbsRc.envs` | `list` | <code>[<br/>&nbsp;&nbsp;{<br/>&nbsp;&nbsp;&nbsp;&nbsp;"name": "KXI_LOG_FORMAT",<br/>&nbsp;&nbsp;&nbsp;&nbsp;"value": "text"<br/>&nbsp;&nbsp;},<br/>&nbsp;&nbsp;{<br/>&nbsp;&nbsp;&nbsp;&nbsp;"name": "KXI_LOG_LEVELS",<br/>&nbsp;&nbsp;&nbsp;&nbsp;"value": "default:debug"<br/>&nbsp;&nbsp;}<br/>]</code> | Default common environment variables for the Resource Coordinator.<br>Additional ENVs can be added and these overridden in custom values files.<br>If overriding `envs`, ensure you include all the default values. |
| `dbsRc.image` | `object` | <code>{}</code> | This sets overriding container image information.<br>Use if you wish to target specific versions of the RC.<br>More information can be found [here](https://kubernetes.io/docs/concepts/containers/images/). |
| `dbsRc.image.tag` | `string` | <code>""</code> | Image tag. |
| `dbsRc.nodeSelector` | `object` | <code>{}</code> | Allows adding node selector constraints to a Pod.<br>This constrains the pods to run only on nodes that match the specified labels.<br>Dictionary of key-value pairs.<br>Refer to [NodeSelector](https://kubernetes.io/docs/concepts/scheduling-eviction/assign-pod-node/#nodeselector). |
| `dbsRc.podAnnotations` | `object` | <code>{}</code> | Custom annotations to be applied to Pod resources.<br>Dictionary of key-value pairs.<br>Refer to [Object Annotations](https://kubernetes.io/docs/concepts/overview/working-with-objects/annotations/). |
| `dbsRc.podLabels` | `object` | <code>{}</code> | Custom labels to be applied to Pod resources.<br>Dictionary of key-value pairs.<br>Refer to [Object Labels](https://kubernetes.io/docs/concepts/overview/working-with-objects/labels/). |
| `dbsRc.resources` | `object` | <code>{}</code> | Resource Coordinator Kubernetes resources.<br>Refer to [Container Resources](https://kubernetes.io/docs/concepts/configuration/manage-resources-containers/). |
| `dbsRc.service` | `object` | <code>{}</code> | Provisions the Kubernetes Service required to expose the workloads.<br>More information can be found [here](https://kubernetes.io/docs/concepts/services-networking/service/). |
| `dbsRc.service.port` | `int` | <code>5040</code> | Set exposed Service Port.<br>Refer to [Service Ports](https://kubernetes.io/docs/concepts/services-networking/service/#field-spec-ports). |
| `dbsRc.service.type` | `string` | <code>"ClusterIP"</code> | This sets the Service type.<br>Setting the type field to `LoadBalancer` provisions a load balancer for your Service.<br>The actual creation of the load balancer happens asynchronously, and information about the provisioned balancer is published in the Service's `.status.loadBalancer`.<br>More information can be found [here](https://kubernetes.io/docs/concepts/services-networking/service/#publishing-services-service-types). |
| `dbsRc.tolerations` | `list` | <code>[]</code> | This is for setting Kubernetes Tolerations to a Pod.<br>This allows the pods to be scheduled on nodes with matching taints.<br>Refer to [Taint and Tolerations](https://kubernetes.io/docs/concepts/scheduling-eviction/taint-and-toleration/). |
| `dbsRc.volumeMounts` | `list` | <code>[]</code> | Allows additional `volumeMounts` to be added to the Resource Coordinator.<br>Refer to [Volumes](https://kubernetes.io/docs/concepts/storage/volumes/). |
| `dbsRc.volumes` | `list` | <code>[]</code> | Allows additional `volumes` to be added to the Resource Coordinator.<br>Refer to [Volumes](https://kubernetes.io/docs/concepts/storage/volumes/). |
| `dbsSg.affinity` | `object` | <code>{}</code> | Allows adding affinities to the Service Gateway.<br>Refer to [Pod Affinity](https://kubernetes.io/docs/concepts/scheduling-eviction/assign-pod-node/#affinity-and-anti-affinity). |
| `dbsSg.customIpcAuth` | `object` | <code>{}</code> | Custom IPC Authorization configuration.<br>Configure the `customIpcAuth` section to enable custom authentication for an IPC connection to the Service Gateway.<br>See the DB Service documentation for more information. |
| `dbsSg.customIpcAuth.enabled` | `bool` | <code>false</code> | Enable/disable custom IPC authorization sidecar. |
| `dbsSg.customIpcAuth.sidecar.authApi` | `string` | <code>"authorize"</code> | Custom authorization function name (defaults to `"authorize"`). |
| `dbsSg.customIpcAuth.sidecar.envs` | `list` | <code>[<br/>&nbsp;&nbsp;{<br/>&nbsp;&nbsp;&nbsp;&nbsp;"name": "QLIC",<br/>&nbsp;&nbsp;&nbsp;&nbsp;"value": "/opt/kx/lic"<br/>&nbsp;&nbsp;}<br/>]</code> | Additional environment variables for the authorization sidecar. |
| `dbsSg.customIpcAuth.sidecar.image` | `object` | <code>{}</code> | Container image for the custom authorization sidecar. |
| `dbsSg.customIpcAuth.sidecar.image.name` | `string` | <code>""</code> | The name of the custom authentication sidecar image. |
| `dbsSg.customIpcAuth.sidecar.image.pullPolicy` | `string` | <code>"IfNotPresent"</code> | Image pull policy.<br>Refer to [Image Pull Policy](https://kubernetes.io/docs/concepts/containers/images/#image-pull-policy). |
| `dbsSg.customIpcAuth.sidecar.image.repository` | `string` | <code>"portal.dl.kx.com/"</code> | Where the custom authentication sidecar image is located. |
| `dbsSg.customIpcAuth.sidecar.image.tag` | `string` | <code>""</code> | The tag of the custom authentication sidecar image. |
| `dbsSg.customIpcAuth.sidecar.port` | `int` | <code>5000</code> | Port for the authorization sidecar IPC connection. |
| `dbsSg.customIpcAuth.sidecar.resources` | `object` | <code>{}</code> | Resource limits and requests for the authorization sidecar.<br>Refer to [Container Resources](https://kubernetes.io/docs/concepts/configuration/manage-resources-containers/). |
| `dbsSg.customIpcAuth.sidecar.useTls` | `bool` | <code>false</code> | Enable TLS for IPC connection to authorization sidecar. |
| `dbsSg.customIpcAuth.sidecar.volumeMounts` | `list` | <code>[]</code> | Additional volume mounts for the authorization sidecar.<br>Refer to [Volumes](https://kubernetes.io/docs/concepts/storage/volumes/). |
| `dbsSg.customIpcAuth.sidecar.volumes` | `list` | <code>[]</code> | Additional volumes for the authorization sidecar.<br>Refer to [Volumes](https://kubernetes.io/docs/concepts/storage/volumes/). |
| `dbsSg.envs` | `list` | <code>[<br/>&nbsp;&nbsp;{<br/>&nbsp;&nbsp;&nbsp;&nbsp;"name": "KXI_LOG_FORMAT",<br/>&nbsp;&nbsp;&nbsp;&nbsp;"value": "text"<br/>&nbsp;&nbsp;},<br/>&nbsp;&nbsp;{<br/>&nbsp;&nbsp;&nbsp;&nbsp;"name": "KXI_LOG_LEVELS",<br/>&nbsp;&nbsp;&nbsp;&nbsp;"value": "default:debug"<br/>&nbsp;&nbsp;},<br/>&nbsp;&nbsp;{<br/>&nbsp;&nbsp;&nbsp;&nbsp;"name": "KXI_SG_CORS_ENABLED",<br/>&nbsp;&nbsp;&nbsp;&nbsp;"value": "false"<br/>&nbsp;&nbsp;},<br/>&nbsp;&nbsp;{<br/>&nbsp;&nbsp;&nbsp;&nbsp;"name": "KXI_SG_CORS_ORIGINS",<br/>&nbsp;&nbsp;&nbsp;&nbsp;"value": ""<br/>&nbsp;&nbsp;},<br/>&nbsp;&nbsp;{<br/>&nbsp;&nbsp;&nbsp;&nbsp;"name": "KXI_SG_USE_SSL",<br/>&nbsp;&nbsp;&nbsp;&nbsp;"value": "0"<br/>&nbsp;&nbsp;}<br/>]</code> | Default common environment variables for the Service Gateway.<br>Additional ENVs can be added and these overridden in custom values files.<br>If overriding `envs`, ensure you include all the default values. |
| `dbsSg.envs[2]` | `object` | <code>{<br/>&nbsp;&nbsp;"name": "KXI_SG_CORS_ENABLED",<br/>&nbsp;&nbsp;"value": "false"<br/>}</code> | Enables CORS headers to allow external domains to access the Service Gateway. |
| `dbsSg.envs[3]` | `object` | <code>{<br/>&nbsp;&nbsp;"name": "KXI_SG_CORS_ORIGINS",<br/>&nbsp;&nbsp;"value": ""<br/>}</code> | Comma separated list of the full FQDN remote domains which are allowed to access the Service Gateway. |
| `dbsSg.envs[4]` | `object` | <code>{<br/>&nbsp;&nbsp;"name": "KXI_SG_USE_SSL",<br/>&nbsp;&nbsp;"value": "0"<br/>}</code> | When `customIpcAuth.enabled` is set to `true`, this defines if the `service.ports.qipcext` port should use SSL. |
| `dbsSg.image` | `object` | <code>{}</code> | This sets overriding container image information.<br>Use if you wish to target specific versions of the Service Gateway.<br>More information can be found [here](https://kubernetes.io/docs/concepts/containers/images/). |
| `dbsSg.image.tag` | `string` | <code>""</code> | Image tag. |
| `dbsSg.nodeSelector` | `object` | <code>{}</code> | Allows adding node selector constraints to the Service Gateway.<br>This constrains the pods to run only on nodes that match the specified labels.<br>Dictionary of key-value pairs.<br>Refer to [NodeSelector](https://kubernetes.io/docs/concepts/scheduling-eviction/assign-pod-node/#nodeselector). |
| `dbsSg.podAnnotations` | `object` | <code>{}</code> | This is for setting Kubernetes Annotations to a Pod.<br>Dictionary of key-value pairs.<br>Refer to [Object Annotations](https://kubernetes.io/docs/concepts/overview/working-with-objects/annotations/). |
| `dbsSg.podLabels` | `object` | <code>{}</code> | This is for setting Kubernetes Labels to a Pod.<br>Dictionary of key-value pairs.<br>Refer to [Object Labels](https://kubernetes.io/docs/concepts/overview/working-with-objects/labels/). |
| `dbsSg.replicaCount` | `int` | <code>1</code> | This sets the number of replicas for the Service Gateway.<br>More information can be found [here](https://kubernetes.io/docs/concepts/workloads/controllers/deployment/#replicas-and-scaling). |
| `dbsSg.resources` | `object` | <code>{}</code> | Service Gateway Kubernetes resources.<br>Refer to [Container Resources](https://kubernetes.io/docs/concepts/configuration/manage-resources-containers/). |
| `dbsSg.service.annotations` | `object` | <code>{}</code> | This sets the service annotations for the Service Gateway.<br>Refer to [Object Annotations](https://kubernetes.io/docs/concepts/overview/working-with-objects/annotations/). |
| `dbsSg.service.ports` | `object` | <code>{<br/>&nbsp;&nbsp;"http": 8080,<br/>&nbsp;&nbsp;"qipc": 5050,<br/>&nbsp;&nbsp;"qipcext": 5051<br/>}</code> | Set list of exposed service ports. |
| `dbsSg.service.ports.http` | `int` | <code>8080</code> | REST/HTTP port |
| `dbsSg.service.ports.qipc` | `int` | <code>5050</code> | TCP/qIPC port |
| `dbsSg.service.ports.qipcext` | `int` | <code>5051</code> | When `customIpcAuth.enabled` is set to `true`, this port is used for the external qIPC connection to the Service Gateway.<br>See the DB Service documentation for more information. |
| `dbsSg.service.type` | `string` | <code>"ClusterIP"</code> | Sets the Service type.<br>Refer to [Service Types](https://kubernetes.io/docs/concepts/services-networking/service/#publishing-services-service-types). |
| `dbsSg.tolerations` | `list` | <code>[]</code> | Allows adding tolerations to the Service Gateway.<br>This allows the pods to be scheduled on nodes with matching taints.<br>Refer to [Taint and Tolerations](https://kubernetes.io/docs/concepts/scheduling-eviction/taint-and-toleration/). |
| `dbsSg.volumeMounts` | `list` | <code>[]</code> | Allows additional `volumeMounts` to be added to the Service Gateway. |
| `dbsSg.volumes` | `list` | <code>[]</code> | Allows additional `volumes` to be added to the Service Gateway. |
| `dbsSidecar` | `object` | <code>{}</code> | Default Sidecar configuration for DB Service gateway chart components. |
| `dbsSidecar.image` | `object` | <code>{}</code> | This sets overriding container image information.<br>Use if you wish to target specific versions of the dbsSidecar.<br>More information can be found [here](https://kubernetes.io/docs/concepts/containers/images/). |
| `dbsSidecar.image.tag` | `string` | <code>"1.18.1"</code> | Image tag. |
| `nameOverride` | `string` | <code>""</code> | This sets the name of the DB ServiceGW deployment.<br>Overriding Chart name.<br>Used when generating resource names. |
| `podSecurityContext` | `object` | <code>{}</code> | Default Pod Security Context for DB Service gateway chart components.<br>Refer to [Pod Security Context](https://kubernetes.io/docs/tasks/configure-pod-container/security-context/#set-the-security-context-for-a-pod). |
| `securityContext` | `object` | <code>{}</code> | Default security context for DB Service gateway chart components.<br>Refer to [Security Context](https://kubernetes.io/docs/tasks/configure-pod-container/security-context/#set-the-security-context-for-a-container). |
