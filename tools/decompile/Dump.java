import jadx.api.*;
import java.io.File;
public class Dump {
  public static void main(String[] a) throws Exception {
    JadxArgs args = new JadxArgs();
    args.setInputFile(new File(a[0]));
    args.setOutDir(new File(a[1]));
    args.setThreadsCount(12);
    args.setSkipResources(false);
    try (JadxDecompiler j = new JadxDecompiler(args)) { j.load(); j.save(); }
  }
}
