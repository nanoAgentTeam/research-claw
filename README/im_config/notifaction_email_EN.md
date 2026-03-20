# Email Push Configuration

## Prerequisites

Before configuring email push notifications, you need to prepare the following:

- An email account that supports SMTP
- The SMTP authorization code for your email (not the login password)

### Common Email SMTP Settings

| Email Provider | SMTP Server            | Port | Encryption  |
| -------------- | ---------------------- | ---- | ----------- |
| QQ Mail        | smtp.qq.com            | 587  | STARTTLS    |
| 163 Mail       | smtp.163.com           | 465  | SSL         |
| Gmail          | smtp.gmail.com         | 587  | STARTTLS    |
| Outlook        | smtp-mail.outlook.com  | 587  | STARTTLS    |

### Obtaining an SMTP Authorization Code

The process varies by email provider. Here's an example using Gmail:

1. Log in to your Gmail account and go to "Settings" → "See all settings"
2. Enable "IMAP access" under the "Forwarding and POP/IMAP" tab
3. Go to your Google Account → "Security" → "2-Step Verification"
4. Under "App passwords", generate a new app password for "Mail"
5. Save the generated password — you'll need it for the SMTP configuration

> **Note**: The SMTP authorization code / app password is different from your regular login password.

## WebUI Configuration

### 1. Start the WebUI

```bash
python cli/main.py gateway
```

### 2. Access the Configuration Page

Visit `http://127.0.0.1:18790/ui/` and click the "Push Management" tab.

### 3. Add SMTP Configuration

In the "Email Push Management" section, click "Add SMTP Configuration":

- **Name**: A friendly name for this SMTP configuration (e.g., "Gmail", "Work Email")
- **Provider Preset**: Select your email provider (QQ / 163 / Gmail / Outlook / Custom) — this auto-fills the SMTP server and port
- **SMTP Server**: The SMTP server address (auto-filled when using a preset)
- **Port**: The SMTP port number (auto-filled when using a preset)
- **Username**: Your email address
- **Password**: Your SMTP authorization code (not the login password)
- **From Email**: The sender email address (usually the same as your username)
- **From Name**: The sender display name (e.g., "Research Claw")
- **TLS Encryption**: Whether to enable TLS (recommended to keep enabled)

### 4. Test the SMTP Configuration

Click the "Test" button and enter a recipient email address:

- If configured correctly, it will show "Sent successfully"
- The recipient inbox will receive a test email

If the test fails, check the following:

- Whether the SMTP server address and port are correct
- Whether the authorization code is correct (not the login password)
- Whether SMTP service is enabled for your email account
- Whether the network can reach the SMTP server

### 5. Add Recipients

After the SMTP configuration is complete, in the "Recipients" section below, click "Add Recipient":

- **Recipient Email**: The email address to receive push notifications
- **SMTP Configuration**: Select the SMTP configuration added in the previous step
- **Remark**: A note to help identify the recipient

### 6. Enable Push Notifications

Make sure the recipient status is "Enabled". Notifications will be automatically sent via email when scheduled tasks run.

## Environment Variable Configuration (Optional)

In addition to the WebUI, you can also configure SMTP via environment variables (as a fallback):

```bash
export CONTEXT_BOT_SMTP_HOST="smtp.gmail.com"
export CONTEXT_BOT_SMTP_PORT="587"
export CONTEXT_BOT_SMTP_USER="your_email@gmail.com"
export CONTEXT_BOT_SMTP_PASS="your_app_password"
export CONTEXT_BOT_SMTP_FROM="your_email@gmail.com"
export CONTEXT_BOT_SMTP_TLS="1"
```

> SMTP profiles configured in the WebUI take priority over environment variables.

## Notes

### Security Tips

- Keep your authorization code safe and do not share it
- It's recommended to use a dedicated email account for push notifications
- Passwords are hidden in API responses — only a "set" flag is shown

### FAQ

#### Q1: Not receiving emails

**Possible causes**:

- Incorrect or expired SMTP authorization code
- SMTP service not enabled for your email account
- Incorrect recipient email address
- Email blocked by the recipient's spam filter
- Network cannot connect to the SMTP server

**Troubleshooting steps**:

1. Click the "Test" button on the SMTP configuration in the WebUI
2. Verify the authorization code is correct
3. Check the recipient's spam/junk folder
4. Check server logs for network errors

#### Q2: How to push to multiple recipients

**Method**: Simply add multiple recipient configurations.

1. In the Recipients section of the WebUI, click "Add Recipient"
2. Enter different recipient email addresses
3. You can use the same SMTP configuration or different ones
4. Use the remark field to distinguish between recipients

#### Q3: What to do when the authorization code expires

Some email providers may invalidate authorization codes for security reasons. To regenerate:

1. Log in to your email provider's web interface
2. Navigate to the SMTP / app password settings
3. Generate a new authorization code
4. Update the password field in the WebUI SMTP configuration
