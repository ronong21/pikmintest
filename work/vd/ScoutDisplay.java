// 폰 화면 밖 '가상 화면'을 만들어 피크민 블룸을 거기서 돌린다 (scrcpy --new-display 와 같은 원리).
// Shizuku(셸 권한)로 실행: CLASSPATH=/data/local/tmp/scout_display.dex app_process / ScoutDisplay <w> <h> <dpi> <폴더> [flags]
// 폴더/id      : 만든 가상 화면 번호 (am start --display, input -d 에 쓴다)
// 폴더/req     : 이 파일이 생기면 지금 화면을 폴더/cap.jpg 로 저장하고 req 를 지운다 (PNG 보다 몇 배 빨라 배터리 절약)
// 폴더/stop    : 이 파일이 생기거나 5분간 요청이 없으면 가상 화면을 닫고 끝낸다
import android.content.AttributionSource;
import android.content.Context;
import android.content.ContextWrapper;
import android.content.pm.ApplicationInfo;
import android.graphics.Bitmap;
import android.graphics.Color;
import android.graphics.PixelFormat;
import android.graphics.drawable.GradientDrawable;
import android.os.Handler;
import android.view.Gravity;
import android.view.WindowManager;
import android.widget.TextView;
import android.hardware.display.DisplayManager;
import android.hardware.display.VirtualDisplayConfig;
import android.os.IBinder;
import android.media.Image;
import android.media.ImageReader;
import android.os.Build;
import android.os.Looper;
import android.os.SystemClock;
import android.view.InputDevice;
import android.view.InputEvent;
import android.view.MotionEvent;

import java.io.File;
import java.io.FileOutputStream;
import java.lang.reflect.Constructor;
import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.nio.ByteBuffer;

public final class ScoutDisplay {
    // 숨은 VIRTUAL_DISPLAY_FLAG_* 값
    static final int PUBLIC = 1, OWN_CONTENT_ONLY = 1 << 3, SUPPORTS_TOUCH = 1 << 6, DESTROY_CONTENT_ON_REMOVAL = 1 << 8,
            TRUSTED = 1 << 10, OWN_DISPLAY_GROUP = 1 << 11, ALWAYS_UNLOCKED = 1 << 12, TOUCH_FEEDBACK_DISABLED = 1 << 13,
            OWN_FOCUS = 1 << 14;
    static final String SHELL = "com.android.shell";

    // 시스템 서비스가 '셸이 부른 것'으로 받아들이도록 패키지 이름을 셸로 맞춘 Context
    static final class ShellContext extends ContextWrapper {
        ShellContext(Context base) { super(base); }
        @Override public String getPackageName() { return SHELL; }
        @Override public String getOpPackageName() { return SHELL; }
        @Override public AttributionSource getAttributionSource() {
            return new AttributionSource.Builder(android.os.Process.SHELL_UID).setPackageName(SHELL).build();
        }
        @Override public Context getApplicationContext() { return this; }
    }

    // scrcpy(Workarounds)와 같은 준비: 시스템 스레드로 표시하고 셸 앱 정보를 채운 뒤 시스템 Context 를 얻는다
    static Context systemContext() throws Exception {
        Class<?> at = Class.forName("android.app.ActivityThread");
        Constructor<?> c = at.getDeclaredConstructor();
        c.setAccessible(true);
        Object t = c.newInstance();
        step("2-1 스레드 준비");
        Field f = at.getDeclaredField("sCurrentActivityThread");
        f.setAccessible(true);
        f.set(null, t);
        Field sys = at.getDeclaredField("mSystemThread");
        sys.setAccessible(true);
        sys.setBoolean(t, true);
        try {  // 일부 기기(삼성 등)는 앱 정보가 비어 있으면 문제 → 셸로 채움
            Class<?> abd = Class.forName("android.app.ActivityThread$AppBindData");
            Constructor<?> bc = abd.getDeclaredConstructor();
            bc.setAccessible(true);
            Object bind = bc.newInstance();
            ApplicationInfo ai = new ApplicationInfo();
            ai.packageName = SHELL;
            Field aif = abd.getDeclaredField("appInfo");
            aif.setAccessible(true);
            aif.set(bind, ai);
            Field bf = at.getDeclaredField("mBoundApplication");
            bf.setAccessible(true);
            bf.set(t, bind);
        } catch (Throwable e) {
            step("2-2 앱 정보 채우기 건너뜀: " + e);
        }
        step("2-3 시스템 Context 요청");
        Method m = at.getDeclaredMethod("getSystemContext");
        m.setAccessible(true);
        Context ctx = (Context) m.invoke(t);
        step("2-4 시스템 Context 받음");
        return ctx;
    }

    // 화면 위에 떠 있는 [■ 버섯 찾기 중지] 버튼. 누르면 출력에 STOP (Termux 는 셸이 만든 파일을 못 읽어서 출력으로 알린다), 폴더/overlay_close 가 생기면 닫힌다
    // 실행: app_process / ScoutDisplay overlay <폴더>   (셸에 SYSTEM_ALERT_WINDOW appop 허용 필요)
    static void overlay(File dir) throws Exception {
        Looper.prepareMainLooper();
        Context ctx = new ShellContext(systemContext());
        WindowManager wm = (WindowManager) ctx.getSystemService(Context.WINDOW_SERVICE);
        TextView v = new TextView(ctx);
        v.setText("■ 버섯 찾기 중지");
        v.setTextSize(15);
        v.setTextColor(Color.WHITE);
        v.setPadding(36, 22, 36, 22);
        GradientDrawable bg = new GradientDrawable();
        bg.setColor(0xDDD32F2F);
        bg.setCornerRadius(60);
        v.setBackground(bg);
        WindowManager.LayoutParams lp = new WindowManager.LayoutParams(
                WindowManager.LayoutParams.WRAP_CONTENT, WindowManager.LayoutParams.WRAP_CONTENT,
                WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
                WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE, PixelFormat.TRANSLUCENT);
        lp.gravity = Gravity.TOP | Gravity.END;
        lp.x = 24;
        lp.y = 260;
        lp.packageName = SHELL;
        File close = new File(dir, "overlay_close");
        close.delete();
        v.setOnClickListener(x -> {
            System.out.println("STOP");
            System.out.flush();
            v.setText("중지하는 중...");
        });
        wm.addView(v, lp);
        System.out.println("overlay shown");
        Handler h = new Handler(Looper.getMainLooper());
        h.post(new Runnable() {
            @Override public void run() {
                if (close.exists()) {
                    wm.removeView(v);
                    System.exit(0);
                }
                h.postDelayed(this, 500);
            }
        });
        Looper.loop();
    }

    public static void main(String[] a) throws Exception {
        if (a[0].equals("overlay")) {
            overlay(new File(a[1]));
            return;
        }
        if (a[0].equals("pinch")) {  // pinch <화면번호> <가운데x> <가운데y> <시작 간격> <끝 간격>
            pinch(Integer.parseInt(a[1]), Float.parseFloat(a[2]), Float.parseFloat(a[3]),
                    Float.parseFloat(a[4]), Float.parseFloat(a[5]));
            return;
        }
        if (a[0].equals("focus")) {  // focus <화면번호>: 그 화면 맨 위 작업(블룸)에 포커스. 원래 포커스였던 작업 번호를 prev 로 출력
            focus(Integer.parseInt(a[1]));
            return;
        }
        if (a[0].equals("focus-task")) {  // focus-task <작업번호>: 그 작업(원래 쓰던 앱)으로 포커스를 돌려줌
            Object atm = atm();
            method(atm, "setFocusedTask", 1).invoke(atm, Integer.parseInt(a[1]));
            System.out.println("focused " + a[1]);
            return;
        }
        int w = Integer.parseInt(a[0]), h = Integer.parseInt(a[1]), dpi = Integer.parseInt(a[2]);
        File dir = new File(a[3]);
        // 폰 화면이 꺼져도 이 화면은 따로 켜져 있도록 OWN_DISPLAY_GROUP + ALWAYS_UNLOCKED (안드로이드 13+)
        int flags = PUBLIC | OWN_CONTENT_ONLY | SUPPORTS_TOUCH | DESTROY_CONTENT_ON_REMOVAL;
        if (Build.VERSION.SDK_INT >= 33) flags |= TRUSTED | OWN_DISPLAY_GROUP | ALWAYS_UNLOCKED | TOUCH_FEEDBACK_DISABLED;
        // OWN_FOCUS + STEAL_TOP_FOCUS_DISABLED(1<<16): 가상 화면을 눌러도 폰 화면의 키보드 입력(지우기 등)을 뺏지 않게
        if (Build.VERSION.SDK_INT >= 34) flags |= OWN_FOCUS | (1 << 16);
        if (a.length > 4) flags = Integer.parseInt(a[4]);

        // 어디서 멈추는지 알 수 있게 단계마다 찍는다
        step("1 시작 (안드로이드 SDK " + Build.VERSION.SDK_INT + ", flags " + flags + ")");
        Looper.prepareMainLooper();
        // 시스템 Context 를 만들면 일부 기기(삼성)가 프로세스를 죽인다 → 디스플레이 서비스(IDisplayManager)에 직접 요청
        step("2 디스플레이 서비스 연결");
        IBinder binder = (IBinder) Class.forName("android.os.ServiceManager")
                .getMethod("getService", String.class).invoke(null, "display");
        Object idm = Class.forName("android.hardware.display.IDisplayManager$Stub")
                .getMethod("asInterface", IBinder.class).invoke(null, binder);
        step("3 콜백 준비");
        Constructor<?> cc = null;
        for (Constructor<?> k : Class.forName("android.hardware.display.DisplayManagerGlobal$VirtualDisplayCallback").getDeclaredConstructors())
            if (k.getParameterCount() == 2) cc = k;
        cc.setAccessible(true);
        Object cb = cc.newInstance(new Object[]{null, null});
        step("4 화면 받을 준비");
        ImageReader reader = ImageReader.newInstance(w, h, PixelFormat.RGBA_8888, 3);
        VirtualDisplayConfig.Builder cb2 = new VirtualDisplayConfig.Builder("scout", w, h, dpi)
                .setFlags(flags).setSurface(reader.getSurface());
        // 발열 줄이기: 안 보이는 화면은 초당 20번만 그리게 (안드로이드 14+, 블룸 그래픽 부하가 크게 줄어듦)
        if (Build.VERSION.SDK_INT >= 34) cb2.setRequestedRefreshRate(20f);
        VirtualDisplayConfig cfg = cb2.build();
        step("5 가상 화면 만들기");
        Method create = null, release = null;
        for (Method mm : idm.getClass().getMethods()) {
            if (mm.getName().equals("createVirtualDisplay") && mm.getParameterCount() == 4) create = mm;
            if (mm.getName().equals("releaseVirtualDisplay") && mm.getParameterCount() == 1) release = mm;
        }
        int id = (Integer) create.invoke(idm, cfg, cb, null, SHELL);
        if (id < 0) throw new IllegalStateException("가상 화면 생성 거부됨: " + id);
        step("6 완료");

        dir.mkdirs();
        File req = new File(dir, "req"), stop = new File(dir, "stop"), cap = new File(dir, "cap.jpg"), tmp = new File(dir, "cap.tmp");
        req.delete();
        stop.delete();
        write(new File(dir, "id"), String.valueOf(id).getBytes());
        System.out.println("display " + id + " " + w + "x" + h + " dpi " + dpi + " flags " + flags);
        System.out.flush();

        Image latest = null;
        long last = System.currentTimeMillis();
        try {
            while (!stop.exists() && System.currentTimeMillis() - last < 300_000) {
                Image img = reader.acquireLatestImage();  // 계속 비워 줘야 새 화면이 들어온다
                if (img != null) {
                    if (latest != null) latest.close();
                    latest = img;
                }
                if (req.exists()) {
                    last = System.currentTimeMillis();
                    if (latest != null) {
                        Image.Plane p = latest.getPlanes()[0];
                        ByteBuffer buf = p.getBuffer();
                        buf.rewind();
                        Bitmap full = Bitmap.createBitmap(p.getRowStride() / p.getPixelStride(), h, Bitmap.Config.ARGB_8888);
                        full.copyPixelsFromBuffer(buf);
                        Bitmap bmp = Bitmap.createBitmap(full, 0, 0, w, h);
                        try (FileOutputStream o = new FileOutputStream(tmp)) {
                            bmp.compress(Bitmap.CompressFormat.JPEG, 92, o);
                        }
                        tmp.renameTo(cap);
                        req.delete();
                    }
                }
                Thread.sleep(100);
            }
        } finally {
            release.invoke(idm, cb);
            reader.close();
            System.out.println("closed");
        }
    }

    // 두 손가락 오므리기/벌리기 (input 명령은 손가락 하나만 돼서 직접 터치를 넣는다. scrcpy 와 같은 방식)
    // from > to 면 오므리기 = 지도 줌아웃
    static void pinch(int display, float cx, float cy, float from, float to) throws Exception {
        Object im;
        try {
            im = Class.forName("android.hardware.input.InputManagerGlobal").getMethod("getInstance").invoke(null);
        } catch (Exception e) {
            im = Class.forName("android.hardware.input.InputManager").getMethod("getInstance").invoke(null);
        }
        Method inject = im.getClass().getMethod("injectInputEvent", InputEvent.class, int.class);
        Method setDisplay = InputEvent.class.getMethod("setDisplayId", int.class);
        MotionEvent.PointerProperties[] pp = new MotionEvent.PointerProperties[2];
        MotionEvent.PointerCoords[] pc = new MotionEvent.PointerCoords[2];
        for (int i = 0; i < 2; i++) {
            pp[i] = new MotionEvent.PointerProperties();
            pp[i].id = i;
            pp[i].toolType = MotionEvent.TOOL_TYPE_FINGER;
            pc[i] = new MotionEvent.PointerCoords();
            pc[i].pressure = 1f;
            pc[i].size = 1f;
        }
        long down = SystemClock.uptimeMillis();
        int steps = 14;
        for (int k = 0; k <= steps + 1; k++) {
            float d = from + (to - from) * Math.min(k, steps) / steps;
            pc[0].x = cx - d; pc[0].y = cy;
            pc[1].x = cx + d; pc[1].y = cy;
            int action, count = 2;
            if (k == 0) {  // 첫 손가락 내림 → 둘째 손가락 내림
                send(inject, setDisplay, im, display, down, MotionEvent.ACTION_DOWN, 1, pp, pc);
                action = MotionEvent.ACTION_POINTER_DOWN | (1 << MotionEvent.ACTION_POINTER_INDEX_SHIFT);
            } else if (k == steps + 1) {  // 둘째 손가락 뗌 → 첫 손가락 뗌
                send(inject, setDisplay, im, display, down, MotionEvent.ACTION_POINTER_UP | (1 << MotionEvent.ACTION_POINTER_INDEX_SHIFT), 2, pp, pc);
                action = MotionEvent.ACTION_UP;
                count = 1;
            } else {
                action = MotionEvent.ACTION_MOVE;
            }
            send(inject, setDisplay, im, display, down, action, count, pp, pc);
            Thread.sleep(20);
        }
        System.out.println("pinch ok");
    }

    static void send(Method inject, Method setDisplay, Object im, int display, long down, int action, int count,
                     MotionEvent.PointerProperties[] pp, MotionEvent.PointerCoords[] pc) throws Exception {
        MotionEvent ev = MotionEvent.obtain(down, SystemClock.uptimeMillis(), action, count, pp, pc, 0, 0, 1f, 1f, 0, 0,
                InputDevice.SOURCE_TOUCHSCREEN, 0);
        setDisplay.invoke(ev, display);
        inject.invoke(im, ev, 0);
        ev.recycle();
    }

    // 다른 앱이 앞에 있으면 가상 화면의 블룸(유니티)은 포커스가 없어 지도 위 버섯 터치를 무시한다
    // → 버섯을 누르는 동안만 블룸 작업에 포커스를 준다 (STEAL_TOP_FOCUS_DISABLED 는 '터치로' 뺏는 것만 막는다)
    static void focus(int display) throws Exception {
        Object atm = atm();
        Object cur = method(atm, "getFocusedRootTaskInfo", 0).invoke(atm);
        System.out.println("prev " + (cur == null ? -1 : cur.getClass().getField("taskId").getInt(cur)));
        java.util.List<?> tasks = (java.util.List<?>) method(atm, "getAllRootTaskInfosOnDisplay", 1).invoke(atm, display);
        if (tasks == null || tasks.isEmpty()) {
            System.out.println("no task on display " + display);
            return;
        }
        int id = tasks.get(0).getClass().getField("taskId").getInt(tasks.get(0));
        method(atm, "setFocusedTask", 1).invoke(atm, id);
        System.out.println("focused " + id);
    }

    static Object atm() throws Exception {
        IBinder b = (IBinder) Class.forName("android.os.ServiceManager").getMethod("getService", String.class).invoke(null, "activity_task");
        return Class.forName("android.app.IActivityTaskManager$Stub").getMethod("asInterface", IBinder.class).invoke(null, b);
    }

    static Method method(Object o, String name, int params) throws NoSuchMethodException {
        for (Method m : o.getClass().getMethods())
            if (m.getName().equals(name) && m.getParameterCount() == params) return m;
        throw new NoSuchMethodException(name);
    }

    static void step(String msg) {
        System.out.println("단계 " + msg);
        System.out.flush();
    }

    static void write(File f, byte[] b) throws Exception {
        try (FileOutputStream o = new FileOutputStream(f)) { o.write(b); }
    }
}
