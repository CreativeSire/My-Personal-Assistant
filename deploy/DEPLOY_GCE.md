# Deploying Victor OS to Google Compute Engine (Spot Instances)

This guide deploys Victor as a cost-effective, always-on "Cloud Node".

## Prerequisities
1.  **Google Cloud SDK** installed and authenticated (`gcloud auth login`).
2.  **Project ID** set (`gcloud config set project YOUR_PROJECT_ID`).

## Step 1: Prepare the Environment
Create a `.env` file in this directory with your secrets (API Keys, Telegram Token).

## Step 2: Deploy the Spot Instance
Run this command to provision a powerful but cheap VM (e2-standard-2).
We use the `startup.sh` script to auto-install everything.

```bash
gcloud compute instances create victor-cloud-node 
    --zone=us-central1-a 
    --machine-type=e2-standard-2 
    --provisioning-model=SPOT 
    --instance-termination-action=STOP 
    --image-family=ubuntu-2204-lts 
    --image-project=ubuntu-os-cloud 
    --metadata-from-file=startup-script=deploy/gce/startup.sh 
    --tags=http-server,https-server
```

## Step 3: Upload Code
Since this is a private personal assistant, we push the code directly instead of using a public git repo.

```bash
# Copy files to the instance
gcloud compute scp --recurse victor_os requirements.txt manage.py .env victor-cloud-node:/opt/victor/ --zone=us-central1-a
```

## Step 4: Reboot to Start
Restart the instance to trigger the startup script and launch Victor.

```bash
gcloud compute instances reset victor-cloud-node --zone=us-central1-a
```

## Monitoring
Check if Victor is alive:
```bash
gcloud compute ssh victor-cloud-node --zone=us-central1-a --command="sudo systemctl status victor"
```
View logs:
```bash
gcloud compute ssh victor-cloud-node --zone=us-central1-a --command="sudo journalctl -u victor -f"
```
