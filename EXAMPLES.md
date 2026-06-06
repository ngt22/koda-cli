# koda examples

A collection of concrete examples across different domains. For a quick start,
see the [highlights in the README](README.md#example-uses).

## Contents

- [Git](#git)
- [Docker / Kubernetes](#docker--kubernetes)
- [Cloud](#cloud)
- [System ops](#system-ops)
- [Development](#development)
- [Cross-machine](#cross-machine)
- [Local LLM](#local-llm)

---

## Git

**Shorten a verbose git log command**

Save a long git log command and run it with a short name.

```bash
koda a "git log --oneline --graph --decorate --all" -t git -s glog
koda x glog
```

**Tag the current commit and push it**

Save a one-liner that tags HEAD and pushes the tag in one step.

```bash
koda a "git tag \$1 \$(git rev-parse --short HEAD) && git push origin \$1" -t git -s tag-push
koda x tag-push -V v1.2.3
```

---

## Docker / Kubernetes

**Capture a container's IP and reuse it immediately**

Pipe `docker inspect` output into koda, then embed the saved value with `$(koda r)`.

```bash
docker inspect app | jq -r '.[0].NetworkSettings.IPAddress' | koda a -t docker
curl http://$(koda r):3000/healthz
```

**Restart any Kubernetes deployment**

Save a rollout restart command with a named placeholder for the service.

```bash
koda a "kubectl rollout restart deployment/\${svc} -n production" -t k8s -s k8s-restart
koda x k8s-restart -V svc=api-gateway
```

**Port-forward to any service**

Save a kubectl port-forward template; supply service and port at call time.

```bash
koda a "kubectl port-forward svc/\${svc} \${port}:80 -n production" -t k8s -s pf
koda x pf -V svc=api,port=8080
```

**Tail error logs from any deployment**

Save a kubectl log pipeline and run it against any service.

```bash
koda a "kubectl logs deploy/\${svc} --tail=200 | grep ERROR" -t k8s -s k8s-errors
koda x k8s-errors -V svc=api
```

---

## Cloud

**Sync a build artifact to any S3 bucket**

Save an `aws s3 sync` command and swap the bucket name at call time.

```bash
koda a "aws s3 sync ./dist s3://\${bucket}/app/ --delete --profile prod" -t aws -s s3-sync
koda x s3-sync -V bucket=my-staging-frontend
```

**Start an SSM session on any EC2 instance**

Save the SSM command with the instance ID as a positional placeholder.

```bash
koda a "aws ssm start-session --target \$1 --region ap-northeast-1" -t aws -s ssm
koda x ssm -V i-0abc1234567def890
```

**Save a generated instance ID and reuse it**

Pipe the ID from `aws ec2 run-instances` into koda, then pass it to follow-up commands.

```bash
aws ec2 run-instances ... | jq -r '.Instances[0].InstanceId' | koda a -t aws -s new-instance
koda x ssm -V $(koda r new-instance)
```

---

## System ops

**Capture a container's dynamic port and reuse it immediately**

A container started with `-P` gets a random host port. Capture it once and embed it across follow-up commands.

```bash
# Start a container and save the randomly assigned host port
docker run -d -P --name web nginx
docker port web 80/tcp | cut -d: -f2 | koda a -t docker -s web-port

# Hit the container from any command — no repeated docker port query
curl http://localhost:$(koda r web-port)/
open http://localhost:$(koda r web-port)/admin
```

**SSH into ephemeral instances with a flag-heavy command**

Ephemeral instances (spot workers, CI runners) don't belong in `.ssh/config`. Save the full command with all flags and substitute the IP at call time.

```bash
koda a "ssh -i ~/.ssh/prod.pem -o StrictHostKeyChecking=no ec2-user@\$1" -t ssh -s ec2
koda x ec2 -V 10.0.1.42
koda x ec2 -V 10.0.1.55
```

**Sync a local directory to a remote server**

Save an rsync command with a named host placeholder.

```bash
koda a "rsync -avz --progress ./dist deploy@\${host}:/var/www/html/" -t deploy -s rsync-deploy
koda x rsync-deploy -V host=prod.example.com
```

**Create a dated backup on every run**

Escape `\$(date)` so it expands at exec time, not at save time.

```bash
koda a "tar czf ~/backups/src-\$(date +%Y%m%d-%H%M).tar.gz ./src" -t backup -s backup
koda x backup   # creates src-20260505-1430.tar.gz each time
```

**Pick a saved host with fzf and substitute it into a command**

Register IP addresses with a `host` tag. Use `koda p -r -t host` to pick one interactively and pass it as a variable into any command template. Or save the full command per host and use `koda p -x` to pick and run in one step.

```bash
# Register IP addresses once
koda a "10.0.1.10" -t host -s web-1
koda a "10.0.1.11" -t host -s web-2
koda a "10.0.1.20" -t host -s db-1
koda a "10.0.1.30" -t host -s bastion
```

**Pattern A — template + pick**: save a command once with `$1`, pick the IP at run time.

```bash
# Save a long command template once
koda a "ssh -i ~/.ssh/prod.pem ec2-user@\$1 'sudo journalctl -u app -n 100 -f'" -t ssh -s taillog

# Open fzf filtered to the host tag, pick a host, run the command
koda x taillog -V $(koda p -r -t host)
```

`koda p -r -t host` opens fzf showing only `host` entries; selecting one prints the IP, which `-V` passes as `$1`.

**Pattern B — one entry per host, pick and exec**: save the full command for each host, then use `koda p -x` to pick and execute in one step.

```bash
# Save the full command for each host
koda a "ssh -i ~/.ssh/prod.pem ec2-user@10.0.1.10 'sudo journalctl -u app -n 100 -f'" -t ssh -s web-1-log
koda a "ssh -i ~/.ssh/prod.pem ec2-user@10.0.1.11 'sudo journalctl -u app -n 100 -f'" -t ssh -s web-2-log
koda a "ssh -i ~/.ssh/prod.pem ec2-user@10.0.1.30 'sudo journalctl -u app -n 100 -f'" -t ssh -s bastion-log

# Pick from the ssh entries and execute immediately
koda p -x -t ssh
```

`koda p -x -t ssh` opens fzf pre-filtered to the `ssh` tag; pressing Enter executes the selected entry directly.

---

## Development

**Open a dashboard for any environment**

Save dashboard URLs for each environment under the same tag, then pick one with fzf.

```bash
koda a "https://grafana.internal/d/prod/main"    -t url,prod    -s grafana-prod
koda a "https://grafana.internal/d/staging/main" -t url,staging -s grafana-staging
koda a "https://grafana.internal/d/dev/main"     -t url,dev     -s grafana-dev

# Open directly by shortcut
xdg-open $(koda r grafana-prod)

# Or pick from all url entries interactively
xdg-open $(koda p -r -t url)
```

**Connect to a database in any environment**

Save connection strings for each environment and pick one with fzf.

```bash
koda a "psql postgres://admin@db.prod.internal:5432/myapp"    -t db,prod    -s db-prod
koda a "psql postgres://admin@db.staging.internal:5432/myapp" -t db,staging -s db-staging
koda a "psql postgres://admin@db.dev.internal:5432/myapp"     -t db,dev     -s db-dev

# Connect to a specific environment
koda x db-prod

# Or pick interactively
koda p -x -t db
```

**Convert video with a saved ffmpeg preset**

Save an ffmpeg encode command with source and output as positional placeholders.

```bash
koda a "ffmpeg -i \$1 -vcodec libx264 -crf 23 \$2" -t media -s h264
koda x h264 -V input.mov,output.mp4
```

**Query a local LLM from the terminal**

Save a curl-based request template via heredoc; supply the prompt at call time.

```bash
koda a -t llm -s gen <<'EOF'
curl -sS http://localhost:11434/api/generate \
  -d '{"model":"llama3","prompt":"$1","stream":false}' | jq -r .response
EOF
koda x gen -V "Explain HTTP/2 server push"
```

**Append a saved snippet to a project file**

Store a reusable multi-line fragment and stream it directly into a file with `koda r`.

```bash
koda a -t infra -s pybase <<'EOF'
FROM python:3.12-slim
RUN pip install uv
WORKDIR /app
EOF
koda r pybase >> Dockerfile
```

---

## Cross-machine

**Share your public SSH key across machines**

Save the key on machine A, push it, then pull and retrieve it on any other machine.

```bash
# Machine A
koda a "$(cat ~/.ssh/id_ed25519.pub)" -t ssh -s pubkey
koda push

# Machine B
koda pull
koda r pubkey   # paste into authorized_keys or GitHub
```

**Keep reusable commands in sync across machines**

Build a library of snippets on one machine and make them available everywhere via Git sync.

```bash
# Machine A — build the library
koda a "kubectl rollout restart deployment/\${svc} -n production" -t k8s -s k8s-restart
koda a "aws ssm start-session --target \$1 --region ap-northeast-1" -t aws -s ssm
koda push

# Machine B — pull and run immediately
koda pull
koda x k8s-restart -V svc=worker
```

---

## Local LLM

**Query a llama.cpp server from the terminal**

llama.cpp exposes an OpenAI-compatible API at `http://localhost:8080`. Save the curl invocation once and supply the prompt at call time.

```bash
koda a -t llm -s llm <<'EOF'
curl -sS http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d "{\"model\": \"llama3\", \"messages\": [{\"role\": \"user\", \"content\": \"$1\"}], \"stream\": false}" \
  | jq -r '.choices[0].message.content'
EOF

koda x llm -V "What is the time complexity of quicksort?"
koda x llm -V "Summarize the last git commit"
```
