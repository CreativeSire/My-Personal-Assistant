from tools import capture_screen
import os

print("📸 Testing Screen Capture...")
result = capture_screen()

if "❌" in result:
    print(f"FAILED: {result}")
else:
    print(f"PASSED: Screenshot saved to {result}")
    if os.path.exists(result):
        size = os.path.getsize(result)
        print(f"File Size: {size} bytes")
    else:
        print("Error: File path returned but file does not exist.")
