## Introduction

This manual explains all features and operations of the Open Research Claw Web Console. It is organized in a "Goal -> Steps -> Done" format so you can get started quickly.

---

## I. Startup and Access

Goal: Start the WebUI and open the console.

Steps:

1. Open a terminal and go to the project directory.
2. Run the startup command:
   python cli/main.py gateway --port 18790
3. Wait for startup to finish. You should see output similar to:
   INFO:     Started server process
   INFO:     Uvicorn running on http://127.0.0.1:18790
4. Open a browser and visit: http://127.0.0.1:18790/ui
5. After the page loads, you will see the Open Research Claw console.

Done when:

- The left sidebar shows a blue robot icon and the "Open Research Claw" title.
- The lower-left corner shows "Gateway Status: Connected" (green indicator).
- The main area shows the "Control Center" page.

---

## II. First-Time Setup

### Goal 1: Configure your first LLM model

Prerequisite: Prepare an API key (for example OpenAI, Anthropic, Gemini).

Steps:

1. In the left sidebar, click "Model Management" (chip icon).
2. Click "+ Add Model Instance" at the top.
3. Fill in the form:
   - Instance ID: Enter a unique ID, for example `gpt4`.
   - Model Name: Enter the model name, for example `gpt-4-turbo`.
   - API Base: Enter the API endpoint, for example `https://api.openai.com/v1`.
   - API Key: Paste your API key.
   - Provider Type: Choose `openai` from the dropdown (for Anthropic-compatible APIs, choose `anthropic`, and use a URL without `/v1`).
4. Click "Add".
5. The new model card appears in the list.
6. Click "Test Connection" on the top-right of that card.
7. Wait for the result. Success is shown as a green message.

Done when:

- The model card appears in the list.
- Connection test succeeds.
- The model is automatically set as the active model (highlighted card border).

---

### Goal 2: Configure messaging accounts (optional)

Note: If you want to interact with agents through Feishu, Telegram, etc., configure messaging accounts.

Configure a Telegram bot

Prerequisite: You already created a bot with `@BotFather` and got the token.

Steps:

1. Click "Messaging Accounts" in the left sidebar (chat bubble icon).
2. Click "+ Add Account".
3. In the platform selector, click the "Telegram" card.
4. Fill in the form:
   - Token: Paste the bot token from `@BotFather`.
5. Click "Add".
6. When the new account card appears, click "Test Connection".
7. After a successful test, enable the account using the top-right toggle on the card.

Done when:

- The Telegram account card appears in the list.
- Connection test succeeds.
- The toggle is in the enabled state (blue).

Configure a Feishu app

Prerequisite: You created a Feishu custom app and have App ID and App Secret.

Steps:

1. On the "Messaging Accounts" page, click "+ Add Account".
2. Select the "Feishu" card.
3. Fill in:
   - App ID: Enter your Feishu App ID.
   - App Secret: Enter your Feishu App Secret.
4. Click "Add".
5. Test the connection and enable it.

Done when: The Feishu account card appears and the connection test succeeds.

---

### Goal 3: Save configuration

Steps:

1. After finishing model/account setup,
2. Click the blue "Save All Changes" button in the top-right corner.
3. Wait for the save success message.

Done when:

- A green message appears: "Configuration saved".
- Configuration is written to `settings.json`.

---

## III. Control Center Features

### Goal: Check system status

Steps:

1. Click "Control Center" in the left sidebar (dashboard icon).
2. Check the currently active model and channel.
3. Check the workspace path.

Visible information:

- Active model dropdown.
- Active channel dropdown.
- Workspace path (click edit icon to modify).

---

### Goal: Back up current configuration

Steps:

1. On the "Control Center" page, locate the "System Settings" area.
2. Click "Back Up Current Configuration".
3. Wait for backup completion.
4. Click "Refresh Backup List".
5. Confirm the new backup file appears in the list.

Done when:

- A new entry appears with file name, timestamp, and size.
- You can click "View" to preview backup content.

---

### Goal: Restore a configuration backup

Steps:

1. Find the backup you want to restore.
2. Click "Restore" on that row.
3. Click "Confirm" in the dialog.
4. Wait for restore completion.
5. The page refreshes and loads the restored configuration.

Done when:

- A message appears: "Configuration restored".
- Model Management and Messaging Accounts show restored values.

---

## IV. Push Management

### Goal: Configure push subscriptions and receive automation notifications

Note: Push subscriptions are used to receive scheduled task execution results.

#### Configure Telegram push

Prerequisite: You have a Telegram Bot Token and Chat ID.

Steps:

1. Click "Push Management" in the left sidebar (bell icon).
2. In "Push Subscriptions", click "+ Add Subscription".
3. Fill in the form:
   - Channel Type: Select Telegram.
   - Bot Token: Enter your bot token.
   - Chat ID: Enter the target chat ID.
   - Note: Optional, for example "My Telegram".
   - Enabled: Keep the toggle on.
4. Click "Save".
5. The new subscription appears in the list.
6. Click "Test" on that subscription row.
7. Check Telegram for the test message.

Done when:

- The new subscription appears in the list.
- Telegram receives the test message: "This is a test push notification".

---

#### Configure email push

Prerequisite: An SMTP email account is required.

Steps - Part 1: Create SMTP configuration

1. On "Push Management", find "SMTP Email Configuration".
2. Click "+ Add SMTP Configuration".
3. Choose a preset or custom option:
   - For QQ Mail, choose the "QQ Mail" preset.
   - For Gmail, choose the "Gmail" preset.
   - Or choose "Custom".
4. Fill in the form:
   - Configuration Name: For example "My QQ Mail".
   - SMTP Host: For example `smtp.qq.com`.
   - Port: For example `587`.
   - Username: Your mailbox address.
   - Password: Mailbox password or app authorization code.
   - Sender Email: Sender mailbox address.
   - Sender Name: Optional display name.
   - Enable TLS: Keep the toggle on.
5. Click "Save".
6. Click "Test" on that config row.
7. Enter a test recipient email and send.
8. Check inbox for the test email.

Steps - Part 2: Create an email subscription

1. In "Email Subscription Targets", click "+ Add Email Subscription".
2. Fill in:
   - Recipient Email: Notification recipient.
   - SMTP Config: Select the config created above.
   - Note: Optional description.
   - Enabled: Keep the toggle on.
3. Click "Save".
4. Click "Test" to verify.

Done when:

- The SMTP config appears in the list.
- The email subscription appears in the list.
- Test email is received.

---

## V. Automation Tasks

### Goal: Create a scheduled task

Scenario: Automatically check paper updates every day at 09:00.

Steps:

1. Click "Automation Tasks" in the left sidebar (clock icon).
2. In the "Select Project" dropdown at the top, choose the target project.
   - If no project appears, click "Refresh Projects".
3. Click "+ Create Task".
4. Fill in the task form:
   - Task ID: Enter a unique ID, for example `daily_check`.
   - Task Name: Enter a descriptive name, for example "Daily Paper Check".
   - Task Type: Select `normal`.
   - Schedule Rule:
   - Select "Daily".
   - Set time to `09:00`.
   - Timezone: Choose your timezone, for example `Asia/Shanghai`.
   - Prompt: Enter task instructions, for example:
     Check recent paper updates and summarize important changes.
   - Enabled: Keep the toggle on.
5. Click "Create".
6. The new task appears in the task list.

Done when:

- The task list shows the new task.
- Task status is "Enabled" (green toggle).
- Schedule displays "Daily 09:00".

---

### Goal: Run a task manually

Steps:

1. Find the task in the list.
2. Click "Run Now" on that row.
3. Wait for execution.
4. Status changes to "Running" (yellow).
5. After completion, status changes to "Success" (green) or "Failed" (red).

Done when:

- The task finishes execution.
- A new record appears in run history.
- If push is configured, you receive the execution result notification.

---

### Goal: View task run history

Steps:

1. On "Automation Tasks", scroll to the "Run History" section.
2. Review history list, including:
   - Task ID
   - Trigger mode (manual/scheduled)
   - Status (success/failure/running)
   - Start time
   - End time
   - Duration
3. Click "View Details" on a record row.
4. The expanded detail area shows full execution content and output.

Done when:

- You can see all historical runs.
- You can inspect full details for each run.

---

### Goal: Edit an existing task

Steps:

1. Find the task in the list.
2. Click "Edit" on that row.
3. The edit form expands under the task row.
4. Modify fields as needed (Prompt, schedule, etc.).
5. Click "Save".
6. The form collapses and task update is complete.

Done when:

- Updated information appears in the task list.
- A "Task updated" message is shown.

---

### Goal: Delete a task

Steps:

1. Find the task to delete.
2. Click "Delete" on that row.
3. Click "Confirm" in the dialog.
4. The task disappears from the list.

Done when:

- The task is no longer shown.
- A "Task deleted" message is shown.

---

### Goal: Associate push subscriptions with a project

Note: Send task results from a specific project to selected push channels.

Steps:

1. On "Automation Tasks", select the target project.
2. Scroll to the "Project Push Subscriptions" section.
3. Click "Link Subscription".
4. In the popup list, check the global subscriptions to link.
5. Click "Save".
6. Linked subscriptions appear in the project subscription list.

Done when:

- Linked subscriptions are shown for the project.
- Project task results are sent to the linked subscriptions.

---

## VI. Real-Time Logs

### Goal: View system runtime logs

Steps:

1. Click "Real-Time Logs" in the left sidebar (terminal icon).
2. The page shows continuously scrolling log output.
3. Logs auto-update with the latest system activity.

Features:

- Auto-scroll to latest logs.
- Display timestamp, log level, and message content.
- Click "Clear Logs" to clear current display.

Done when:

- You can see real-time system logs.
- Logs update automatically as system activity occurs.

---

## VII. UI Personalization

### Goal: Switch theme mode

Steps:

1. Find the theme switch group in the top-right corner.
2. Three options:
   - System: Follow OS theme.
   - Light: Force light mode.
   - Dark: Force dark mode.
3. Click the mode you want.
4. The UI switches immediately.

Done when:

- UI colors change.
- Selected button is highlighted.

---

### Goal: Switch light theme palette

Prerequisite: You are currently in light mode.

Steps:

1. To the left of theme mode buttons, find the "Palette" dropdown.
2. Three palette options:
   - Slate Soft: Cool gray tones (default).
   - Warm Paper: Warm paper-like tones.
   - Blue Mist: Soft blue tones.
3. Select your preferred palette.
4. The new palette is applied immediately.

Done when:

- Background and text colors in light mode change.
- The palette better matches your preference.

---

### Goal: Switch interface language

Steps:

1. Find the language switch button in the top-right corner.
2. Two options:
   - Chinese
   - English
3. Click your target language.
4. UI text switches immediately.

Done when:

- All UI text changes to the selected language.
- Selected language button is highlighted.

---

## VIII. Advanced Operations

### Goal: Bulk-operate tasks

Scenario: Enable/disable/delete multiple tasks at once.

Steps:

1. In the task list on "Automation Tasks",
2. Check the header checkbox to select all,
   - Or check specific tasks individually.
3. Bulk action buttons appear at the top:
   - Bulk Run: Run all selected tasks now.
   - Bulk Enable: Enable all selected tasks.
   - Bulk Disable: Disable all selected tasks.
   - Bulk Freeze: Freeze all selected tasks.
   - Bulk Delete: Delete all selected tasks.
4. Click the action you need.
5. Click "Confirm" in the dialog.
6. Wait for completion.

Done when:

- Status of all selected tasks is updated.
- A success message is shown.

---

### Goal: Change workspace path

Steps:

1. On "Control Center", locate "Workspace Path".
2. Click the edit icon (pencil) to the right of the path.
3. The input becomes editable.
4. Enter the new workspace path.
5. Click "Save" (or press Enter).
6. Path update completes.

Done when:

- The new workspace path is displayed.
- Configuration is saved.

---

### Goal: View and manage sensitive information

Note: Sensitive fields (API keys, passwords, etc.) are hidden by default.

Steps:

1. On Model Management, Messaging Accounts, Push Management, and similar pages,
2. Find sensitive fields displayed as `********`.
3. Click the eye icon next to the field.
4. Sensitive data is shown in plain text.
5. Click again to hide it.

Done when:

- You can view full API keys/passwords.
- You can switch visibility on demand.

---

## IX. Troubleshooting

### Problem: Gateway connection failed

Symptom: The page shows a "Gateway Closed" overlay.

Resolution steps:

1. Check whether the Gateway process is still running in terminal.
2. If it stopped, rerun:
   python cli/main.py gateway --port 18790
3. Wait for Gateway startup completion.
4. The page will auto-detect and reconnect.
5. The overlay disappears and normal usage resumes.

Done when:

- The lower-left corner shows "Connected" (green).
- All features are usable.

---

### Problem: Model connection test failed

Possible causes:

- Wrong API key.
- Wrong API Base URL.
- Network issue.
- Wrong provider type.

Resolution steps:

1. Click "Edit" on the model card.
2. Check and correct:
   - API key completeness and correctness.
   - API Base URL protocol (must include `https://`).
   - Provider type matching the endpoint.
3. Click "Save".
4. Click "Test Connection" again.
5. Read the error message and adjust accordingly.

Done when:

- Connection test succeeds.
- A green success message appears.

---

### Problem: Push test failed

For Telegram:

- Verify Bot Token.
- Verify Chat ID.
- Ensure the bot has been added to the target chat.

For email:

- Verify SMTP configuration.
- Verify username and password.
- Verify TLS settings.
- Verify port (`587` for TLS, `465` for SSL in common setups).

Resolution steps:

1. Click "Edit" on the subscription.
2. Check and correct all fields.
3. Click "Save".
4. Click "Test" again.
5. Read the error message and adjust accordingly.

Done when:

- Test succeeds.
- Test message is received.

---

### Problem: Task execution failed

How to inspect failure reason:

1. On "Automation Tasks", locate the "Run History" section.
2. Find the failed run record (red "Failed" label).
3. Click "View Details".
4. Read detailed error info and logs.
5. Adjust task config or prompt based on error details.

Common issues:

- Prompt is unclear or ambiguous.
- Model configuration is incorrect.
- Project path problem.
- Permission problem.

Done when:

- Root cause is identified.
- Task runs successfully after adjustment.

---

## X. Best Practices

### Configuration management

1. Back up regularly: Create a backup in "Control Center" before major changes.
2. Test before save: After adding models/accounts, test connection first, then save.
3. Use descriptive names: Give model instances, tasks, and subscriptions clear names and notes.

### Task management

1. Start simple: Create small test tasks first, then move to complex ones.
2. Schedule reasonably: Avoid overly frequent schedules to prevent resource waste.
3. Monitor history: Check run history regularly and fix issues early.
4. Use push notifications: Configure subscriptions for important projects.

### Security recommendations

1. Protect sensitive data: Do not expose screens containing API keys in public places.
2. Rotate credentials regularly: Update API keys and passwords periodically.
3. Restrict access: Gateway listens on `127.0.0.1` by default. Avoid changing it to `0.0.0.0` unless necessary.
4. Back up critical data: Regularly back up configuration and automation task settings.

---

## Appendix: Quick Actions Reference

| Function | Location | Quick Action |
| --- | --- | --- |
| Save configuration | Top-right corner on any page | Click blue "Save All Changes" |
| Switch theme | Top-right corner on any page | Click `System` / `Light` / `Dark` |
| Switch language | Top-right corner on any page | Click `Chinese` / `English` |
| Show sensitive info | Next to sensitive fields | Click the eye icon |
| Test connection | Model/account/subscription cards | Click "Test Connection" or "Test" |
| Run task manually | Task list | Click "Run Now" |
| View run details | Run history list | Click "View Details" |
| Refresh project list | Automation Tasks page | Click "Refresh Projects" |
| Create config backup | Control Center | Click "Back Up Current Configuration" |

---

## Closing Notes

This manual covers the core features and workflows of the Open Research Claw WebUI. If you run into issues:

1. Check the "Real-Time Logs" page for runtime status.
2. Check Gateway connection state in "Control Center".
3. Refer to the "Troubleshooting" section in this guide.
4. Read other project docs under `README/guide/`.

Enjoy using Open Research Claw.
