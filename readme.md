## Support the Project ☕

If this project has helped you, consider supporting its development! Any contribution is highly appreciated.

* **Bitcoin (BTC):**  
  `bc1qkuggdkxa4mydvlra0z66x7ry36tzzed23jj8zk`
* **Ethereum (ETH) / EVM Networks:**  
  `0xaEFab3A093De4Dfa6092DAca9Bf376b2e36cb797`


# Klipper Plugin Manager

A simple web tool for installing and managing small plugins for Klipper 3D printers.

This guide will help you install the Plugin Manager and the Lube Reminder plugin on your Raspberry Pi or another computer running Klipper.

**Don't worry if you are new to Linux!** Follow the steps in order and copy the commands carefully.

## What You Need

Before you start, make sure you have:

* A 3D printer running Klipper.
* A Raspberry Pi or another computer running Klipper.
* Mainsail or Fluidd installed.
* SSH access to your printer.
* These two Python files:

  * `klipper_plugin_manager.py`
  * `lube_reminder.py`

This guide assumes you installed Klipper using KIAUH and that your username is `pi`.

## Step 1: Find Your Printer's IP Address

You need your printer's IP address to connect to it.

1. Open Mainsail or Fluidd in your web browser.
2. Find your printer's IP address in the dashboard or your router settings.

It will look something like this:

`192.168.1.123`

Your actual address will probably be different. Use your own IP address in the commands below.

## Step 2: Copy the Files to Your Printer

On your own computer, open a terminal.

On Windows, you can use PowerShell. On macOS or Linux, use Terminal.

Go to the folder containing the two downloaded Python files, then run:

```bash
scp klipper_plugin_manager.py lube_reminder.py pi@192.168.1.123:~/
```

Replace `192.168.1.123` with your printer's IP address.

If asked whether you trust the connection, check that the address is correct before accepting. Then enter your printer's SSH password if requested.

**What just happened?**

The two files were copied to your printer's home folder.

## Step 3: Connect to Your Printer

In the same terminal, connect to your printer:

```bash
ssh pi@192.168.1.123
```

Replace the example IP address with your printer's address.

You are now typing commands directly on the printer's computer, not your own computer.

Create a folder for the Plugin Manager:

```bash
mkdir -p ~/plugin_manager
```

Move the manager file into that folder:

```bash
mv ~/klipper_plugin_manager.py ~/plugin_manager/
```

The Lube Reminder file can stay in your home folder for now.

## Step 4: Start the Plugin Manager

Run this command:

```bash
python3 ~/plugin_manager/klipper_plugin_manager.py
```

The program should display an address and an access token.

Keep this terminal window open while the program is running.

On your computer, open a web browser and visit:

`http://192.168.1.123:8088`

Replace the example IP address with your printer's IP address.

Enter the access token shown in the terminal.

If the Plugin Manager page opens, congratulations! The first part is working.

**Important:** Do not share your access token with other people. It protects access to the Plugin Manager.

When you finish testing the page, return to the terminal and press `Ctrl+C` to stop the program.

## Step 5: Make the Plugin Manager Start Automatically

Now we will make the Plugin Manager start whenever your printer's computer boots.

This step needs administrator permission.

Create a service file:

```bash
sudo nano /etc/systemd/system/klipper-plugin-manager.service
```

A text editor will open.

Copy and paste the following text into it:

```ini
[Unit]
Description=Klipper Plugin Manager
After=network.target moonraker.service

[Service]
User=pi
ExecStart=/usr/bin/python3 /home/pi/plugin_manager/klipper_plugin_manager.py
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

**Save the file in Nano:**

1. Press `Ctrl+O` to save.
2. Press `Enter` to confirm the filename.
3. Press `Ctrl+X` to close the editor.

Now tell Linux to load the new service and start it:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now klipper-plugin-manager
```

Check that it is running:

```bash
systemctl status klipper-plugin-manager
```

Look for `active (running)`.

If you see it, the Plugin Manager is running in the background.

Press `q` to leave the status screen.

### Find Your Access Token

If you need your token again, run:

```bash
cat ~/.klipper_plugin_manager_token
```

Keep this token private.

## Step 6: Install the Lube Reminder Plugin

Now for the useful part!

Open the Plugin Manager in your browser:

`http://192.168.1.123:8088`

Replace the example IP address with your printer's IP address.

Then follow these steps:

1. Find the section called **Upload .py File**.
2. Choose the `lube_reminder.py` file from your computer.
3. Click **Install**.
4. Read the message shown by the Plugin Manager.
5. If you want to change the lubrication reminder interval, click **Configure**.
6. When you are ready, click **Restart Klipper**.

The Plugin Manager should create a configuration file named:

`extras/lube_reminder.cfg`

It should also add this line to `printer.cfg`:

```ini
[include extras/*.cfg]
```

This tells Klipper to load configuration files from the `extras` folder.

**Important:** Check the installation message before restarting Klipper. If the include line already exists, it should not need to be added again.

## Step 7: Test the Lube Reminder

Open the Mainsail or Fluidd console.

Type this command:

```text
LUBE_STATUS
```

Press Enter.

You should see a message similar to:

```text
Printed 0.0 hours out of 200 hours
```

The exact wording may be different.

This means the plugin is tracking printer usage and reminding you when lubrication is due.

### If Your Printer Has Already Been Used

If your printer has already been running for a while, you can set its recorded usage.

For example, to set it to 50 hours:

```text
LUBE_SET HOURS=50
```

Use the correct number for your printer. This command changes the recorded value; it does not measure your printer's real operating time.

### After Lubricating Your Printer

Follow your printer manufacturer's maintenance instructions.

After you have finished lubricating the printer, run:

```text
LUBE_DONE
```

This tells the plugin that maintenance is complete.

## Troubleshooting

### The Plugin Manager Page Does Not Open

Check that:

* Your printer and computer are connected to the same network.
* You used the correct IP address.
* The Plugin Manager service is running.

Check the service with:

```bash
sudo systemctl status klipper-plugin-manager
```

### The Plugin Manager Says Moonraker Is Unavailable

Moonraker is the service that lets Klipper communicate with web interfaces and other tools.

Check that Moonraker is running:

```bash
sudo systemctl status moonraker
```

If Klipper does not restart from the Plugin Manager, try restarting Klipper through Mainsail or Fluidd instead.

Do not change Moonraker's network security settings just to make the plugin work.

### Permission Denied

The Plugin Manager needs permission to write files into your Klipper installation.

Make sure the service runs as the same Linux user that owns your Klipper files. This guide assumes that user is `pi`.

The plugin file is normally installed in:

`~/klipper/klippy/extras/`

If your Klipper installation is in a different folder, the paths may need to be changed.

### Klipper Will Not Start After Installation

Don't panic. First, check the Klipper log.

The log is commonly located at:

`~/printer_data/logs/klippy.log`

Also check `printer.cfg` for mistakes in the new include line.

The Plugin Manager may create a backup of your configuration named something like:

`printer.cfg.bak-<date>`

If you need to restore a backup, stop and check which file is the correct backup before replacing anything. Keep a copy of your current configuration first.

### Check the Plugin Manager Logs

If the service is not working, run:

```bash
journalctl -u klipper-plugin-manager -f
```

This shows the service's log messages as they appear.

Press `Ctrl+C` to stop watching the logs.

## If Your Installation Uses Different Folders

This guide assumes:

* Linux username: `pi`
* Klipper folder: `/home/pi/klipper`
* Configuration folder: `/home/pi/printer_data/config`
* Plugin Manager folder: `/home/pi/plugin_manager`
* Web port: `8088`

If your folders are different, you may need to change the service's `ExecStart` command and the Plugin Manager's configuration options.

For example, the program may support options like these:

```bash
python3 ~/plugin_manager/klipper_plugin_manager.py --config-dir /home/pi/klipper_config --klipper-dir /home/pi/klipper --port 8088
```

Use the paths that actually exist on your printer. The example above is not a command you should copy unchanged unless those paths match your setup.

## You're Done!

You have installed the Klipper Plugin Manager and the Lube Reminder plugin.

You can now check your printer's lubrication status using:

```text
LUBE_STATUS
```

And record completed maintenance using:

```text
LUBE_DONE
```

Keep your printer maintained, keep your configuration backed up, and enjoy printing!



## Install the Heat Soak Guard Plugin

The Heat Soak Guard plugin makes sure your printer is warm enough before printing starts.

This is useful for enclosed 3D printers. Some printers need time to warm up their frame and enclosure before they can print reliably.

The plugin checks two things:

1. **Frame temperature:** The frame must reach your chosen temperature.
2. **Bed temperature:** The heated bed must stay close to its target temperature for a set amount of time.

Printing continues only when both conditions are met.

### Important: You Need a Frame Temperature Sensor

Before installing this plugin, make sure your printer has a temperature sensor that measures the frame or enclosure temperature.

You must also know the name of that sensor in your Klipper configuration.

For example, your `printer.cfg` might contain:

```ini
[temperature_sensor frame]
sensor_type: Generic 3950
sensor_pin: PC0
```

This is only an example. **Do not copy these sensor settings into your printer configuration unless they match your actual sensor and wiring.** Incorrect sensor settings can cause problems.

If your printer does not have a frame temperature sensor configured in Klipper, you must set one up first.

### Step 1: Install the Plugin

1. Open the Klipper Plugin Manager in your browser.
2. Find the section called **Upload .py File**.
3. Select `heat_soak_guard.py`.
4. Click **Install**.
5. Read the installation message.

The plugin manager should install the file into Klipper's extras folder.

If the installer does not add the configuration automatically, follow the manual configuration instructions below.

### Step 2: Configure the Plugin

Open your `printer.cfg` file in Mainsail or Fluidd.

Add this configuration:

```ini
[heat_soak_guard]
frame_sensor: temperature_sensor frame
frame_target: 40
frame_tolerance: 3
bed_tolerance: 2
bed_minutes: 10
```

**Check the `frame_sensor` name!** The value must match the name of your actual sensor section in Klipper.

For example, if your configuration contains `[temperature_sensor enclosure]`, use:

```ini
frame_sensor: temperature_sensor enclosure
```

Here is what the settings mean:

| Setting           | Meaning                                                  | Example                    |
| ----------------- | -------------------------------------------------------- | -------------------------- |
| `frame_sensor`    | Name of the frame temperature sensor                     | `temperature_sensor frame` |
| `frame_target`    | Desired frame temperature, in °C                         | `40`                       |
| `frame_tolerance` | How far the frame temperature may differ from the target | `3`                        |
| `bed_tolerance`   | How far the bed temperature may differ from its target   | `2`                        |
| `bed_minutes`     | How long the bed must stay within its temperature range  | `10`                       |

With these example settings, the frame must reach between **37°C and 43°C**, and the bed must stay within **2°C of its target temperature for 10 minutes**.

The plugin also supports optional settings:

```ini
# frame_minutes: 0
# report_seconds: 10
# wait_timeout_minutes: 180
# bed_heater: heater_bed
# enabled: True
# guard_start: False
# mode: wait
```

Lines beginning with `#` are comments and are ignored by Klipper. Remove the `#` to enable a setting.

For most users, the basic configuration is enough.

### Step 3: Restart Klipper

Save your configuration and restart Klipper from Mainsail or Fluidd.

If Klipper reports a configuration error, check that:

* The plugin file was installed correctly.
* The `[heat_soak_guard]` section is spelled correctly.
* The frame sensor name matches your actual configuration.
* Your sensor is working and appears in Klipper.

### Step 4: Test the Plugin

Open the Mainsail or Fluidd console and run:

```text
HEAT_SOAK_STATUS
```

This shows the current frame and bed temperatures and whether the printer is ready to print.

You can also run:

```text
HEAT_SOAK_WAIT
```

The printer will wait until both temperature conditions are satisfied.

If the bed is not heating, the command will report an error. Set the bed temperature before running the command.

For example:

```gcode
M140 S100
M190 S100
HEAT_SOAK_WAIT
```

This sets the bed target to 100°C, waits for the bed to reach its target, and then waits for the frame temperature and bed temperature hold time.

**Use a bed temperature appropriate for your printer and filament.** The example value of 100°C is not suitable for every printer or material.

### Step 5: Add It to Your Start G-code

To run the heat soak check automatically before every print, add `HEAT_SOAK_WAIT` to your slicer's start G-code.

Place it after the bed heating commands and before the printer starts printing.

For example:

```gcode
M140 S100
M190 S100
HEAT_SOAK_WAIT

; Continue with the rest of your start G-code
```

Keep the rest of your existing start G-code. Do not replace the entire start sequence with this short example.

When a print starts, the plugin will wait until the frame is warm enough and the bed has stayed within its temperature range for the required time.

If the temperatures are already correct, the command will continue without unnecessary waiting.

### Useful Commands

| Command                         | What it does                                                |
| ------------------------------- | ----------------------------------------------------------- |
| `HEAT_SOAK_WAIT`                | Waits until the frame and bed are ready                     |
| `HEAT_SOAK_STATUS`              | Shows temperatures and readiness                            |
| `HEAT_SOAK_WAIT FRAME=45`       | Temporarily changes the frame target to 45°C for this wait  |
| `HEAT_SOAK_WAIT BED_MINUTES=15` | Requires 15 minutes of stable bed temperature for this wait |
| `HEAT_SOAK_WAIT TIMEOUT=60`     | Sets a maximum wait of 60 minutes for this wait             |
| `HEAT_SOAK_SET ENABLE=0`        | Disables the guard until Klipper restarts                   |
| `HEAT_SOAK_SET ENABLE=1`        | Enables the guard again                                     |

You can combine temporary settings:

```text
HEAT_SOAK_WAIT FRAME=45 BED_MINUTES=15 TIMEOUT=60
```

This waits for a frame temperature of 45°C and 15 minutes of stable bed temperature, with a maximum wait of 60 minutes.

### Optional: Check Every Print Start

By default, the plugin checks temperatures only when `HEAT_SOAK_WAIT` is called.

You can optionally enable automatic checking when a print is started from Mainsail or Fluidd.

Add these settings to your `[heat_soak_guard]` section:

```ini
guard_start: True
mode: wait
```

With `mode: wait`, a print that starts too early is held until the temperatures are ready.

With `mode: reject`, the plugin rejects a print start if the printer is not ready.

**Important:** Automatic start checking is an advanced option. Test it carefully before using it for normal printing.

When a print is waiting, you can use these commands:

```text
HEAT_SOAK_STATUS
HEAT_SOAK_SKIP
HEAT_SOAK_CANCEL
```

* `HEAT_SOAK_STATUS` shows the current state.
* `HEAT_SOAK_SKIP` allows a waiting print to start immediately.
* `HEAT_SOAK_CANCEL` cancels the waiting print.

The last two commands are intended for automatic start checking and do not replace the normal print cancellation controls.

### Safety Notes

* Never leave a heated printer unattended unless your printer and setup are designed for safe unattended operation.
* Use temperature limits appropriate for your printer and materials.
* Do not disable thermal protection or other printer safety features.
* While `HEAT_SOAK_WAIT` is actively waiting, normal pause and cancel commands may not respond as expected. Avoid using this command without understanding how to stop the printer safely.
* `M112` is Klipper's emergency shutdown command. Use it only when an emergency stop is necessary.

If you are unsure about a setting, ask an experienced printer user to check your configuration before printing.
