import uos

# List all files and their sizes
def list_files():
    for file in uos.listdir():
        stats = uos.stat(file)
        print(f"{file}: {stats[6]} bytes")

# Call the function to list files
list_files()
