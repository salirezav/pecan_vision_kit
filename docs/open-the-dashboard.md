# Open the dashboard

Use these steps to show the kit’s dashboard in a web browser on your laptop. You do not need to install anything for the first method.

The examples below use kit **08**. Your label has its own number. If the label says `usda-vision-03`, use `03` everywhere this page uses `08`.

## What you need

- The Raspberry Pi kit
- The USB-C power adapter that belongs with the kit
- An Ethernet cable (the network cable with a wide plastic clip on each end)
- A laptop, either Windows or Mac

If the laptop has no Ethernet socket, use a USB Ethernet adapter: plug the adapter into the laptop, then plug the cable into the adapter.

Leave the laptop joined to its usual Wi-Fi. The cable is a second, separate connection and does not replace Wi-Fi.

## Find the kit’s name

Look for a label stuck on the Raspberry Pi. The name looks like this:

`usda-vision-08`

Write that whole name down. You will type it several times. The letters are lowercase. The number is the two digits on that label.

## The sign-in on this kit

The kit’s sign-in is:

| | |
|---|---|
| Username | `pecan` |
| Password | the name on the label, then `1234`, with nothing in between |

The password is the kit’s own name plus `1234`. A kit named `usda-vision-08` has the password `usda-vision-081234`. A kit named `usda-vision-03` has the password `usda-vision-031234`.

The dashboard in the browser does not ask for this password. You need it when you open the kit’s desktop with VNC, later in these steps.

## 1. Connect the Ethernet cable

1. Plug one end of the Ethernet cable into the Raspberry Pi.
   The Ethernet socket is the wider network socket. It is not one of the rectangular USB sockets.
   Push the plug in until the clip clicks.
2. Plug the other end into the laptop (or into the USB Ethernet adapter).

A small light next to the Pi’s Ethernet socket usually turns on or blinks when the cable is seated. If you do not see a light, unplug that end and push it in again until it clicks.

## 2. Power on the Raspberry Pi

1. Plug the USB-C power adapter into the wall.
2. Plug the small oval USB-C end into the Raspberry Pi’s power socket.
   USB-C is the small oval plug. The larger rectangular sockets on the Pi are USB sockets for other devices, not the power socket.
3. A red light on the Pi comes on. A green light may blink while the Pi starts.
4. Wait **two minutes** before you open the browser. The Pi is still starting during that time. Leave the power and the Ethernet cable connected while you wait.

## 3. Give the Pi an address and internet

Do this while the Ethernet cable is connected. The laptop stays on its usual Wi-Fi. That Wi-Fi is where the laptop’s internet comes from. Sharing sends that internet down the cable to the Pi.

Two things happen when sharing is on:

- The laptop gives the Raspberry Pi an address on the cable automatically. You do not type an address into the Pi.
- The Pi can reach the internet through the laptop.

The camera is not part of this. It is a USB camera and does not get an address.

If the Pi was already powered on before you turn sharing on, unplug the Ethernet cable from the laptop and plug it back in. The Pi then asks the laptop for an address.

**On Windows**

This switch is called Internet Connection Sharing.

1. The laptop is joined to Wi-Fi, and that Wi-Fi has internet. Open a web page you have used before, to be sure.
2. Press the **Windows** key and **R** together. A small Run box opens.
3. Type `ncpa.cpl` and press Enter. A window named Network Connections opens. If Windows asks for permission, click **Yes**.
4. Find the icon named **Wi-Fi**. Right-click it, click **Properties**, then click the **Sharing** tab.
5. Check **Allow other network users to connect through this computer’s Internet connection**.
6. If you see **Home networking connection**, choose the cable, not Wi-Fi. It is usually named **Ethernet**. With a USB Ethernet adapter it may be named **Ethernet 2** or include the adapter’s brand. To tell which one: unplug the cable, look for the icon that says it is disconnected, plug the cable back in, and choose that name.
7. Click **OK**. If Windows asks you to confirm, click **Yes**.

Windows then sets the laptop’s end of the cable to `192.168.137.1` and gives the Pi a different address that starts with `192.168.137.`

Check the laptop. Open the Start menu, type `cmd`, and press Enter. In the black window, type `ipconfig` and press Enter. Under the Ethernet adapter you chose, **IPv4 Address** should be `192.168.137.1`. That number is the laptop, not the Pi. If that line still starts with `169.254.`, sharing is not on. Repeat the Sharing steps, then unplug the cable and plug it back in.

The Pi’s own address is the other `192.168.137.` number. To see it, type `ping -4 usda-vision-08.local` and press Enter. Use your kit’s number. A line that says `Reply from 192.168.137.40` means the Pi’s address is `192.168.137.40`. Your last number will be different. `192.168.137.1` is always the laptop, so do not use `.1` as the Pi.

**On a Mac**

This switch is called Internet Sharing.

1. The Mac is joined to Wi-Fi, and that Wi-Fi has internet. Open a web page you have used before, to be sure.
2. Click the Apple menu at the top left of the screen, then **System Settings**.
3. Click **General** in the sidebar, then **Sharing**. You may need to scroll the sidebar.
4. Find **Internet Sharing**. If its switch is already on, turn it off. The choices cannot be edited while it is on.
5. Click **Configure**, or the round **i** button, next to Internet Sharing.
6. Set **Share your connection from** to **Wi-Fi**.
7. Under **To devices using**, turn on the cable. It is named **Ethernet**, or **USB**, or the brand of a USB Ethernet adapter. Leave **Wi-Fi** off in this list. Wi-Fi is the source, not the port you are sharing to.
8. Click **Done**.
9. Turn **Internet Sharing** on. If the Mac warns that sharing may affect other people on the network, click **Start** or **OK**. Sharing Wi-Fi out through the cable to this one Pi is what you want. The Mac’s Wi-Fi stays connected.

The Mac then uses `192.168.2.1` on its end of the cable and gives the Pi a different address that starts with `192.168.2.`

Check the Mac. Press **Command** and **Space**, type `Terminal`, and press Return. Type `ping usda-vision-08.local` and press Return. Use your kit’s number. A line that says `64 bytes from 192.168.2.2` means the Pi’s address is `192.168.2.2`. Your last number will be different. `192.168.2.1` is the Mac, not the Pi. The command keeps running. Press **Control** and **C** together to stop it.

If the only address you see is full of colons, wait half a minute, unplug the cable, plug it back in, and run the same command again.

**Confirm the Pi has internet**

You can do this after the Pi’s desktop is open, in the section that opens that desktop. On the Pi’s own screen, open Terminal and type `ping -c 3 google.com`, then press Enter. Lines that say a time in milliseconds mean the Pi reached the internet through the laptop. `Network is unreachable` or `unknown host` means sharing is not on yet, or the cable was not plugged back in after you turned sharing on.

You can keep using the name address, `http://usda-vision-08.local:8080`. The number the laptop gave the Pi also opens the dashboard, for example `http://192.168.137.40:8080` on Windows or `http://192.168.2.2:8080` on a Mac. Add `:8080` either way.

## 4. Open the dashboard

1. On the laptop, open a web browser. Safari, Chrome, Edge, and Firefox all work.
2. Click the address bar. That is the long box at the top of the window, where a website address normally appears.
3. Type this address, with your kit’s number in place of `08`, and press Enter:

   `http://usda-vision-08.local:8080`

You should see the dashboard, including a live picture from the camera.

The camera is a USB camera plugged into the Raspberry Pi. It does not have an address, and it does not join Wi-Fi. Every address on this page belongs to the Raspberry Pi. The picture is the USB camera, shown by the Pi.

Type the address yourself. Do not search for it. Each part has a job:

| Part | What to type | Why it is there |
|---|---|---|
| `http://` | those exact characters | Tells the browser to open a page. Without this, the browser may search the web instead. |
| `usda-vision-08` | the name on the label | The name of this kit. |
| `.local` | those exact characters | Tells the laptop to look for that name on the cable. The label does not include `.local`. You add it. |
| `:8080` | colon, then 8080 | The dashboard’s port. Without `:8080` the browser looks in the wrong place and the page does not open. |

For kit 03 the address is `http://usda-vision-03.local:8080`.

## If the dashboard does not open

Try these in order.

1. Wait one more minute, then press Enter on the address again.
2. Check the cable. Both ends are clicked in. The laptop is awake and not asleep.
3. Check the address character by character: `http://`, the label name, `.local`, and `:8080`. There are no spaces. There is no `www`. Use `http://`, not `https://`.
4. Ask the laptop whether it can see the Pi. Use the steps for the computer you have.

**On Windows**

1. Open the Start menu, type `cmd`, and press Enter.
2. In the black window, type `ping usda-vision-08.local` and press Enter. Use your kit’s number.
3. Lines that say `Reply from` mean the laptop can see the Pi. Go back to the browser and open the dashboard address again.
4. `Ping request could not find host` means the laptop cannot find that name yet. Wait one minute and run the same command again. Compare the name with the label.

**On a Mac**

1. Press the **Command** key and the **Space** bar together. A search box opens.
2. Type `Terminal` and press Return.
3. In the window that opens, type `ping usda-vision-08.local` and press Return. Use your kit’s number.
4. Lines that say `64 bytes from` mean the Mac can see the Pi. This command keeps running. Press the **Control** key and **C** together to stop it.
5. Go back to the browser and open the dashboard address again.
6. `Unknown host` or `cannot resolve` means the Mac cannot find that name yet. Wait one minute and run the same command again. Compare the name with the label.

The name address works while the Ethernet cable connects the laptop and the Pi. Use the next section when that address stops opening the dashboard, or when you want an address that keeps working on Wi-Fi without the cable.

## 5. A lasting address, using VNC and Wi-Fi

Use this when `http://usda-vision-08.local:8080` no longer opens. You will look at the Pi’s own desktop from the laptop, join the Pi to the same Wi-Fi as the laptop, read the Pi’s Wi-Fi address, and use that address from then on.

Keep the Ethernet cable connected until the Wi-Fi address opens the dashboard. The VNC connection in this section uses the cable.

### Open the Pi’s desktop

VNC is a way to show the Pi’s screen on the laptop. The Pi is powered on, the Ethernet cable is still connected, and you have waited until the Pi has finished starting.

**On Windows**

1. Download **RealVNC Viewer** (the free viewer) from [realvnc.com](https://www.realvnc.com/en/connect/download/viewer/) and install it with the default choices. If a VNC app is already installed, use that.
2. Open the VNC app.
3. In the server or address box, type `usda-vision-08.local:5900`. Use your kit’s number.
   If the app has a separate port box, type `usda-vision-08.local` as the address and `5900` as the port.
4. Click **Connect**.

**On a Mac**

The Mac already includes this. You do not need to install an app.

1. Click the desktop so you are in Finder, not inside another app.
2. In the menu bar at the top of the screen, click **Go**, then **Connect to Server**. You can also press the **Command** key and **K** together.
3. In the server address box, type `vnc://usda-vision-08.local:5900`. Use your kit’s number.
4. Click **Connect**.

If that menu is missing, download **RealVNC Viewer** for Mac from [realvnc.com](https://www.realvnc.com/en/connect/download/viewer/) and connect to `usda-vision-08.local:5900` the same way as on Windows.

**After the connection opens, on either computer**

1. A warning about an unknown certificate, an untrusted connection, or a hostname mismatch is expected. The Pi created its own certificate. Choose **Continue**, **Yes**, or **Accept**.
2. Sign in with the kit sign-in from above:

   | | |
   |---|---|
   | Username | `pecan` |
   | Password | `usda-vision-081234` on kit 08 |

   Use your own label. There is no space before `1234`, and the letters stay lowercase. This is the kit sign-in. It is not the Wi-Fi password.

A desktop appears inside the VNC window. That is the Pi’s screen. Clicks and typing in that window go to the Pi. Clicks outside that window go to your laptop.

If the VNC app says the connection was refused, wait two minutes and try `usda-vision-08.local:5900` again. The desktop is still starting.

### Join the kit to your Wi-Fi

The laptop is already on the Wi-Fi network you want the kit to use. You need that network’s password. It is the password for the building or home Wi-Fi, not `pecan` and not the `1234` password.

Inside the VNC window:

1. Look at the **top-right corner of the Pi’s desktop**.
2. Click the network icon. It is a pair of arrows, or a fan-shaped Wi-Fi mark.
3. A list of Wi-Fi names opens. Click the **same name** the laptop is connected to.
4. Type the Wi-Fi password and confirm.
5. Wait until the icon shows that the Pi is connected. This often takes about half a minute.

### Read the kit’s Wi-Fi address

Still inside the VNC window:

1. Open a terminal on the Pi. Click the terminal icon in the Pi’s top bar (a black rectangle). If you do not see it, click the Raspberry menu at the **top left** of the Pi’s screen, then **Accessories**, then **Terminal**.
2. Click inside the terminal so the cursor is there.
3. Type `ip a` and press Enter. There is a space between `ip` and `a`.
4. The reply is several blocks. Find the block whose first line contains `wlan0`. That block is the Pi’s Wi-Fi connection.
5. Inside the `wlan0` block, find the line that starts with `inet` and a number like `inet 192.168.137.64/24`.
6. The address is the four numbers **before** the slash. In this example the address is `192.168.137.64`. Your numbers will be different. Write them down. This address belongs to the Raspberry Pi. The USB camera does not have one.

Skip these lines. They are not the address to put in the browser:

- `inet 127.0.0.1` is the Pi itself
- a line under `eth0` is the Ethernet cable, not the Wi-Fi

### Open the dashboard with that address

On the laptop, in the laptop’s own browser (not in the VNC window), click the address bar and open:

`http://192.168.137.64:8080`

Use the four numbers you wrote down, then `:8080`. Keep `http://`.

The dashboard appears. After it loads, you can close the VNC window and unplug the Ethernet cable. Leave the Pi plugged into power, and leave it in range of that Wi-Fi.

## The next time you open the dashboard

1. Power on the Pi and wait two minutes.
2. The laptop is on the same Wi-Fi the Pi joined.
3. Open `http://` plus the four numbers you wrote down, plus `:8080`.

You do not need the Ethernet cable or VNC for this.

A router can give the Pi a new number later. If the saved number stops opening the page, connect the Ethernet cable again and repeat the VNC section to read the new `wlan0` address.

## Which address to use

| When | Address | Example for kit 08 |
|---|---|---|
| First connection, cable plugged in | label name, `.local`, port `8080` | `http://usda-vision-08.local:8080` |
| Laptop is sharing internet over the cable | the number the laptop gave the Pi, port `8080` | Windows: `http://192.168.137.40:8080`. Mac: `http://192.168.2.2:8080` |
| Opening the Pi’s desktop | label name, `.local`, port `5900` | `usda-vision-08.local:5900` |
| After the Pi is on your Wi-Fi | the four `wlan0` numbers, port `8080` | `http://192.168.137.64:8080` |

The dashboard port is always `8080`. The desktop (VNC) port is always `5900`.
