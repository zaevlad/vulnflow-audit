# Configuration (`conf.yaml`)

VulnFlow uses one main configuration file: `conf.yaml`.

This file lives in the project root, next to `vulnflow.py`.

It is used for two things:

- model providers in the `models` section
- external REST tools in the `tools` section

If `conf.yaml` is missing, invalid, or incomplete, the UI may show no providers, no tools, or both.

---

## Where the file is

Path:

`<project_root>/conf.yaml`

Example:

```text
vulnflow.py
conf.yaml
dashboard/
docs/
```

After editing `conf.yaml`, restarting VulnFlow is the safest way to make sure the new values are used.

---

## Basic structure

The file usually looks like this:

```yaml
models:
  openrouter:
    enabled: true
    api_key_env: OPENROUTER_API_KEY
    supports_response_format: true
    models:
      - openai/gpt-4o-mini

tools:
  - name: example_api
    base_url: https://api.example.com/v1
    endpoints:
      - name: search
        path: search
        method: POST
        parameters:
          - name: query
            type: string
            required: true
```

---

## Models

The `models` section tells VulnFlow which AI providers and models it can use in Agent blocks.

Only these provider IDs are supported by the code:

- `chatgpt`
- `claude`
- `openrouter`
- `ollama`
- `lmstudio`
- `llama_cpp`

If you add another provider name, VulnFlow will ignore it.

### Simple example

```yaml
models:
  chatgpt:
    enabled: true
    api_key_env: OPENAI_API_KEY
    models:
      - gpt-4o-mini

  ollama:
    enabled: true
    supports_response_format: false
    models:
      - llama3.1
```

### Common fields

| Field | What it does |
|------|---------------|
| `enabled` | Turns the provider on or off. If the provider section exists and `enabled` is not set, VulnFlow treats it as enabled. |
| `models` | List of model names shown in the UI. |
| `api_key` | API key written directly in `conf.yaml`. |
| `api_key_env` | Name of an environment variable that stores the API key. |
| `base_url` | Custom API URL. |
| `base_url_env` | Name of an environment variable that stores the API URL. |
| `supports_response_format` | Tells VulnFlow whether the provider should use strict JSON response formatting support. |

### What is really required

In practice, a provider is useful only when:

- the provider name is one of the supported IDs
- it is enabled
- `models` contains at least one non-empty model name
- the provider has the authentication it needs

For local providers such as `ollama`, `lmstudio`, and `llama_cpp`, an API key is usually not required.

### How API keys are resolved

For model providers, VulnFlow looks for the API key in this order:

1. `api_key`
2. `api_key_env`
3. the provider's default environment variable

Default provider environment variables used by the code:

- `chatgpt` -> `OPENAI_API_KEY`
- `claude` -> `ANTHROPIC_API_KEY`
- `openrouter` -> `OPENROUTER_API_KEY`

Local providers do not have a default API key variable in the code.

### How base URLs are resolved

For model providers, VulnFlow looks for the base URL in this order:

1. `base_url`
2. `base_url_env`
3. `<PROVIDER>_BASE_URL`
4. built-in default URL

Examples:

- `chatgpt` -> `CHATGPT_BASE_URL`
- `claude` -> `CLAUDE_BASE_URL`
- `openrouter` -> `OPENROUTER_BASE_URL`
- `ollama` -> `OLLAMA_BASE_URL`

### Default base URLs

If you do not set a custom base URL, VulnFlow uses these defaults:

| Provider | Default URL |
|------|--------------|
| `chatgpt` | `https://api.openai.com/v1` |
| `claude` | `https://api.anthropic.com/v1` |
| `openrouter` | `https://openrouter.ai/api/v1` |
| `ollama` | `http://localhost:11434` |
| `lmstudio` | `http://localhost:1234/v1` |
| `llama_cpp` | `http://localhost:8080/v1` |

### `supports_response_format`

This setting matters when VulnFlow asks a model to return JSON.

If `supports_response_format` is `true`, VulnFlow may use the provider's structured JSON response option.

If it is `false`, VulnFlow adds a plain instruction telling the model to return valid JSON only.

Default behavior in the code:

- `chatgpt` -> `true`
- `claude` -> `true`
- `openrouter` -> `true`
- `ollama` -> `false`
- `lmstudio` -> `false`
- `llama_cpp` -> `false`

You can override this in `conf.yaml`.

---

## Tools

The `tools` section is used for external REST APIs.

These tools can be attached to an Agent block through its child `Tool` node. During a run, the model can call the selected tool if tool calling is available.

`tools` must be a list.

### Tool example

```yaml
tools:
  - name: example_api
    description: Example REST service
    base_url: https://api.example.com/v1
    auth_type: api_key_header
    api_key_header: X-API-Key
    api_key: your-secret-key
    endpoints:
      - name: search
        path: search
        method: POST
        description: Search documents
        parameters:
          - name: query
            type: string
            required: true
            description: Search query
```

### Tool fields

| Field | Required | What it does |
|------|----------|---------------|
| `name` | Yes | Tool name shown in the UI. |
| `base_url` | Yes | Base URL for all endpoints in this tool. |
| `description` | No | Extra description for the model. |
| `auth_type` | No | Authentication mode. |
| `api_key` | No | Secret used for authentication. |
| `api_key_header` | Only for `api_key_header` auth | Header name for the API key. |
| `endpoints` | Yes | List of callable API endpoints. |

If `name` or `base_url` is missing, the tool is ignored.

### Supported authentication modes

The code supports these tool auth modes:

- `bearer`
- `api_key_header`

How they work:

- `bearer` adds `Authorization: Bearer <api_key>`
- `api_key_header` adds `<api_key_header>: <api_key>`

If auth values are missing, the request is still built, but without the expected auth header.

### Important limitation

For `tools`, the code reads only `api_key` from `conf.yaml`.

There is currently no `api_key_env` support for tools in the tool-loading code.

That means this will **not** work for tools unless you add your own code support:

```yaml
api_key_env: MY_SECRET
```

For now, tool secrets must be stored directly in `conf.yaml` if you want VulnFlow to send them automatically.

---

## Endpoints

Each tool contains an `endpoints` list.

Each endpoint becomes one callable function for the model.

The internal function name is built like this:

`<tool_name>_<endpoint_name>`

Example:

- tool name: `example_api`
- endpoint name: `search`
- function name used internally: `example_api_search`

### Endpoint fields

| Field | Required | What it does |
|------|----------|---------------|
| `name` | Yes | Endpoint name. |
| `path` | Yes | URL path under `base_url`. |
| `method` | No | HTTP method. Default is `GET`. |
| `description` | No | Description shown to the model. |
| `parameters` | No | List of input parameters. |

### Parameter fields

Each endpoint parameter can have:

- `name`
- `type`
- `required`
- `description`

Supported parameter types in the code:

- `string`
- `integer`
- `number`
- `boolean`
- `array`
- `object`

If an unknown type is used, VulnFlow falls back to `string`.

### How requests are sent

If the endpoint method is:

- `GET` or `DELETE` -> parameters are sent as query parameters
- anything else, such as `POST` -> parameters are sent as JSON body

The request always starts from:

`base_url + path`

### Path parameters

Endpoint paths can include placeholders like:

```text
contracts/{address}
```

In that case, the model must provide a matching argument such as `address`.

If a required path parameter is missing, the tool call fails.

---

## Common mistakes

These are the most common configuration problems:

- `conf.yaml` is invalid YAML
- the provider name is not one of the supported IDs
- `models` exists but contains no usable model names
- cloud provider has no API key
- wrong `base_url`
- tool has no `name` or no `base_url`
- tool endpoint has no `name` or no `path`
- tool secret was written as `api_key_env`, but tools do not support that yet

---

## Security notes

- Do not commit real API keys if the repository is shared.
- For model providers, prefer `api_key_env` instead of writing secrets directly in `conf.yaml`.
- For tools, be careful: the current code reads only `api_key` directly from the YAML file.
- Remember that `conf.yaml` can contain sensitive information.

---

## Related files

- [`../readme.md`](../readme.md)
- [`agent-block.md`](agent-block.md)
- [`pipeline-save-and-load.md`](pipeline-save-and-load.md)
- [`../vulnflow_project.md`](../vulnflow_project.md)
