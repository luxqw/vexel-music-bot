# Fix for HTTP 403 Forbidden Issue

## Problem
The bot would work fine initially, but after running for some time, it would start getting HTTP 403 Forbidden errors when trying to play YouTube videos. A restart would fix the issue temporarily.

## Root Cause
The bot was caching YouTube stream URLs, which expire after a certain time. When the bot tried to use these expired URLs, YouTube would return 403 Forbidden errors.

## Solution
The fix implements fresh stream URL extraction at play time:

1. **Changed Data Storage**: Instead of storing the stream URL (`url`), we now store the original video URL (`original_url`)
2. **Fresh Extraction**: When playing a track, the bot extracts a fresh stream URL from the original video URL
3. **Better Error Handling**: Added retry logic and automatic fallback to the next track if extraction fails
4. **Enhanced Logging**: Added detailed logging with emojis for easier debugging

## Key Changes in Code

### Before:
```python
# Stored stream URL that expires
track = {
    "title": info["title"], 
    "url": info["url"],  # Stream URL - expires!
    "requester": interaction.user.name,
}

# Used expired URL directly
source = create_source(next_track["url"])
```

### After:
```python
# Store original video URL
track = {
    "title": info["title"],
    "original_url": info.get("original_url", info.get("webpage_url", info["url"])),  # Original URL
    "requester": interaction.user.name,
}

# Extract fresh stream URL at play time
source = create_source(next_track["original_url"], next_track["title"])
```

## Benefits
- ✅ No more 403 Forbidden errors from expired URLs
- ✅ No need to restart the bot
- ✅ Better error handling and recovery
- ✅ Improved logging for debugging
- ✅ Automatic queue progression on failures

## Logs You'll See
- `🔍 Получаем свежий stream URL для: Song Title`
- `✅ Получен свежий stream URL для: Song Title`  
- `🎵 Играет: Song Title`
- `❌ Не удалось получить аудио для: Song Title` (if extraction fails)

This fix ensures the bot continues working reliably without requiring restarts due to expired YouTube sessions.