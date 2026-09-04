# KDB-X DB Service - Public Preview 1

The DB Service is a self-contained variation of the KDB-X [tick-based architecture](https://code.kx.com/kdb-x/how_to/manage_streaming_data/architecture.html), with many useful built-in features accessible via a well-documented API. It is a simple way to quickly deploy a fully-implemented KDB-X database to capture and query streaming and batch data.

![KDB-X DB Service architecture](architecture.png)

Full documentation available at [code.kx](https://code.kx.com/kdb-x/services/db-service/introduction.html)

## Prerequisites

Before starting the DB Service, you must have your local environment ready. Ensure the following:

- **License**. You have a valid [KDB-X license](https://code.kx.com/kdb-x/get_started/kdb-x-install.html#license-requirements). Set in the `.env` file. If it is a Community Edition license it should be assigned to `KDB_LICENSE_B64`. For a k4 license, use `KDB_K4LICENSE_B64`. *NOTE: if you're using a Community Edition license from before May 2026, you'll need to generate a new one to use the DB Service. You can get a new automatically-generated license by visiting the [KDB-X Install page](https://developer.kx.com/products/kdb-x/install).*

- **Docker**, with `docker compose` available. Refer to the [Docker installation](https://docs.docker.com/get-started/get-docker/) guide and the [`docker compose` installation](https://docs.docker.com/compose/install/) instructions.

- **KDB-X installed**, if you'd like to interact with the service via q. Refer to the [KDB-X installation guide](https://code.kx.com/kdb-x/get_started/kdb-x-install.html) for instructions.

## Configuring usage restrictions

DB Service is configured by default to enforce the [usage restrictions](https://code.kx.com/insights/licensing/usage-restrictions.html) of the KDB-X Community Edition license. If you are not using Community Edition you should edit `.env` and delete the indicated variables (`DS_SM_MEM_LIMIT`, `DS_DA_MEM_LIMIT` and `DS_OTHER_MEM_LIMIT`) to disable the restrictions.

If you are using Community Edition you are permitted to edit these restrictions so long as you remain within the 16GB total limit. If you are running on a typical Linux system, the [systemd slice](#using-systemd-slices-for-community-edition-usage-limits) approach outlined below is more flexible.

## Starting the DB Service

The DB Service runs as a containerized service and is distributed using Docker Compose.

To start the DB service:

1. Clone this repo.

2. Log in to the KX docker registry. If you don't already have an account, you can sign up for free at the [KX Developer Center](https://developer.kx.com). A login token can be created at https://portal.dl.kx.com/auth/token, if you need to generate a new one.

```bash
EMAIL=email@example.com
BEARER=BEARERTOKEN
docker login -u $EMAIL -p $BEARER portal.dl.kx.com
```

3. Add your license to the `.env` file (see note above):

```bash
export KDB_LICENSE_B64=LICENSE
```

4. Run the initialization script - this command initializes the database directories, and copies sample data to the import path:

```bash
./init-db.sh
```

5. Start the service:

```bash
docker compose up -d
```

## Using the DB Service

You can connect to the DB Service using one of the following interfaces. There is a basic import and query workflow available in the documentation's [quickstart](https://code.kx.com/kdb-x/services/db-service/quickstart.html). Example notebooks are also bundled with the service.

### q client

Download the q client from the [q client repo](https://github.com/KxSystems/kdbx-db-service-q-client) and load it into your q session.

```q
dbs:use`kx.dbservice_client
```

Full client documentation and usage examples are available in the [q client repo](https://github.com/KxSystems/kdbx-db-service-q-client).

### Python client

The Python client can be installed directly from https://portal.dl.kx.com.

```bash
pip install --pre --extra-index-url https://portal.dl.kx.com/assets/pypi/ kdbx_db_service_client
```

Full client documentation and usage examples are available in the [Python client repo](https://github.com/KxSystems/kdbx-db-service-python-client). For additional KDB-X Python installation and environment setup, refer to the [KDB-X Python install guide](https://code.kx.com/kdb-x/get_started/kdb-x-python-install.html).

### REST API

You can interact with the DB Service using HTTP requests. Send requests to the service endpoint on port `8080`. For example:

```bash
curl -X GET "http://localhost:8080/api/v0/tables"
```

For full request and response examples, refer to the [OpenAPI documentation](https://code.kx.com/kdb-x/services/db-service/api/dbservice.html).

## OpenAPI docs

The OpenAPI source and generated Redoc page live under `openapi/`.

When updating the API spec or Redoc assets, regenerate `openapi/dbservice.html` from the `openapi/` directory:

```bash
cd openapi
npx @redocly/cli@latest lint dbservice.yaml
npx @redocly/cli@latest build-docs dbservice -t redoc-custom.hbs -o dbservice.html
python3 -m http.server 8081
```

Then open `http://localhost:8081/dbservice.html`.

## Example notebooks

Notebooks are included in this repo with end-to-end examples using the q client, Python client, and cURL.

- `notebooks/qClient_notebook.ipynb`
- `notebooks/python_notebook.ipynb`
- `notebooks/curl_notebook.ipynb`

### Notebook setup

1. Install the requirements into a virtual environment

```bash
cd notebooks
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

2. Launch Jupyter

```bash 
jupyter lab
```

Ensure DB Service is running at `http://localhost:8080`, then open the URL printed by Jupyter.

## Sample data feed

There is a sample data feed in the `samples/data` directory, demonstrating how to use the Python RT library to send streaming data to the DB Service. To run the sample feed:

1. Install dependencies

```bash
cd samples/data
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

2. Create the `fxquote` table. This is required before starting the feed:

```bash
curl -s -X POST "http://localhost:8080/api/v0/tables/fxquote" \
  -H "Accept: application/json" \
  -H "Content-Type: application/json" \
  -d '{
    "type": "partitioned",
    "prtnCol": "ts",
    "columns": [
      {"name": "trddate", "type": "date"},
      {"name": "ts", "type": "timestamp"},
      {"name": "sym", "type": "symbol"},
      {"name": "bid", "type": "float"},
      {"name": "ask", "type": "float"}
    ]
  }'
```

3. Run the sample feed in a separate terminal from the running DB Service or client session. Alternatively, run the feed in the background with `python3 fxfeed.py &`.

```bash
python3 fxfeed.py
```

The feed runs continuously, publishing FX quote data to the `fxquote` table until you stop it with `Ctrl+C`. It prints a confirmation message when it starts.

To verify that data is being published, run the following row-count query while the feed is active. Run the query again after a few seconds and confirm that the `rowCount` value has increased.

```bash
curl -s -X POST "http://localhost:8080/api/v0/query/q" \
  -H "Accept: application/json" \
  -H "Content-Type: application/json" \
  -d '{"query": "select rowCount:count i from fxquote"}'
```

## Using systemd slices for Community Edition usage limits

If you have root access and are on a systemd-based Linux system you can use its slices functionality to restrict the total memory usage of the whole service rather than the default approach of configuring individual limits. To enable it, run:

```bash
sudo cp kx-db-service.slice /etc/systemd/system/
```

Then edit `.env` and uncomment the line

```
#export DS_CGROUP_PARENT=kx-db-service.slice
```

You should also disable the individual CE memory limits, as they are unnecessary when using the slice.

## Resetting the service

⚠️ Resetting the service permanently deletes all data and RT client logs, and re-initializes directories. If you need to reset the database, run:

```bash
 docker compose down 
./reset-db.sh
```
