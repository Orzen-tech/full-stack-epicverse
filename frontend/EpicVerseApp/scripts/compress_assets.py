import os
from PIL import Image

# Path to your assets images
ASSETS_DIR = r"e:\kriyora\EpicVerse\frontend\EpicVerseApp\assets\images"

def compress_images():
    print(f"🚀 Starting EpicVerse Asset Optimization in {ASSETS_DIR}...")
    
    for filename in os.listdir(ASSETS_DIR):
        if filename.endswith(".png") or filename.endswith(".jpg"):
            file_path = os.path.join(ASSETS_DIR, filename)
            initial_size = os.path.getsize(file_path) / (1024 * 1024)
            
            if initial_size < 1.0:
                # print(f" - skipping {filename} (already under 1MB)")
                continue
                
            print(f" - optimizing {filename} ({initial_size:.2f}MB)...")
            
            try:
                img = Image.open(file_path)
                # Convert to RGB if needed to save as JPEG/WebP or just save as tight PNG
                # But to stay truly 'LOGIC-SAFE', we must keep the extension as .png 
                # even if we use lossy compression inside.
                
                # Option A: Save as optimized PNG (lossless)
                img.save(file_path, optimize=True, quality=85)
                
                new_size = os.path.getsize(file_path) / (1024 * 1024)
                print(f"   -> result: {new_size:.2f}MB (Saved {initial_size - new_size:.2f}MB)")
            except Exception as e:
                print(f"   !! Error optimizing {filename}: {e}")

    print("\n" + "="*50)
    print("EPICVERSE ASSET OPTIMIZATION COMPLETE")
    print("="*50)
    print("Your app should now build and load images significantly faster.")
    print("Visual parameters: Lossy PNG 85% | In-place overwrite.")
    print("="*50)

if __name__ == "__main__":
    compress_images()
