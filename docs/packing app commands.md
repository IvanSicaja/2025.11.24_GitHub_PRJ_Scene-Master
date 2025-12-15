1. Install PyInstaller
pip install pyinstaller
2. Navigate to the folder where your main.py file is located:
cd "C:\User..."
3. Create a standalone executable with icon
pyinstaller --onefile --windowed --name SceneMaster --icon=..\assets\media\icons\icon.ico --distpath publish --workpath publish\build --specpath publish main\main.py


4. Optional for removing non necessary files -> run in pycharm terminal
Get-ChildItem -Path .\publish -Recurse | Where-Object { $_.Extension -ne '.exe' } | Remove-Item -Force -Recurse