# Push Notification Configuration

### 1. Add a Bot to Your Group

In a DingTalk group chat, click the group settings button in the top-right corner, then click "Robots".

![1772801852477](image/dingtalk_push_ZH/1772801852477.png)

![1772801887125](image/dingtalk_push_ZH/1772801887125.png)

### 2. Add a Custom Bot

Click "Add Robot".

![1772801937420](image/dingtalk_push_ZH/1772801937420.png)

Click "Custom".

![1772801979578](image/dingtalk_push_ZH/1772801979578.png)

Click "Add".

![1772802016759](image/dingtalk_push_ZH/1772802016759.png)

Give it a name → Select "Sign" for security settings → Agree to the terms → Click "Finish".

Remember to copy the signing secret.

![1772802128326](image/dingtalk_push_ZH/1772802128326.png)

Copy the webhook URL, then click "Finish".

![1772802157078](image/dingtalk_push_ZH/1772802157078.png)

## WebUI Configuration

### 1. Start the WebUI

```bash
python cli/main.py gateway
```

### 2. Access the Configuration Page

Visit `http://127.0.0.1:18790/ui/` and click the "Push Management" tab.

Click the "Add Subscription" button, then select "DingTalk" as the channel.

- Paste the full webhook URL copied from DingTalk
- Example: `https://oapi.dingtalk.com/robot/send?access_token=your_token`
- Paste the signing secret
- Add a note to help identify different push configurations
- Example: Dev group notifications, Production alerts, etc.
  ![1772803178006](image/dingtalk_push_ZH/1772803178006.png)

### 5. Test the Push

Click the "Test" button. If configured correctly:

- The interface will show "Sent"
  ![1772803621552](image/dingtalk_push_ZH/1772803621552.png)
- You will receive a test message in the DingTalk group
  ![1772803675146](image/dingtalk_push_ZH/1772803675146.png)

  If the test fails, check the following:
- Whether the webhook URL is correct
- If keywords are set, whether the test message contains the keyword
- Whether the bot has been removed or disabled

### 6. Enable Push Notifications

After a successful test, make sure the subscription status is "Enabled". This way, notifications will be automatically pushed to the DingTalk group when scheduled tasks run.

## Notes

### Rate Limits

DingTalk bots have the following limitations:

- Each bot can send a maximum of 20 messages per minute
- Exceeding the limit will return an error and the message will fail to send
- It is recommended to set a reasonable execution frequency for scheduled tasks

### Error Handling

Common errors and solutions:

| Error Message               | Cause                        | Solution                                                   |
| --------------------------- | ---------------------------- | ---------------------------------------------------------- |
| `keywords not in content` | Message doesn't contain keyword | Check the keyword in security settings and ensure the message includes it |
| `invalid token`           | Token is incorrect or expired | Re-obtain the webhook URL                                  |
| `sign not match`          | Signature verification failed | Switch to keyword verification method                      |
| `request limit`           | Rate limit exceeded          | Reduce push frequency, wait one minute and retry           |

## FAQ

### Q1: Not receiving push messages

**Possible causes**:

- Incorrect webhook URL
- Bot has been removed or disabled
- Rate limit exceeded
- Network connection issues
- Incorrect signing secret (if using signature mode)

**Troubleshooting steps**:

1. Click the "Test" button in the WebUI and check the error message returned
2. Check if the bot is still in the DingTalk group
3. Check if a large number of messages were sent in a short period
4. If using signature mode, verify the secret is correct
5. Check server logs for network errors

### Q2: How to push to multiple groups

**Method**: Create an independent bot and push configuration for each group.

1. Add a custom bot in each DingTalk group separately
2. Obtain the webhook URL for each bot
3. Add multiple push subscriptions in the WebUI, one for each group
4. Use the note field to distinguish between different groups
