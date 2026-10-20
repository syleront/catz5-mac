// Export only what tools/recomp/translate.py reads: functions.tsv and
// functions/<addr>.asm. The kit's own ExportProgram.java also runs the
// decompiler for every function and writes functions/<addr>.c + strings.tsv;
// the translator uses the .c only for an advisory jump-table cross-check
// (translate.py cross_check_switches, a stat, not a decision), so skipping the
// decompiler removes the dominant cost of exporting hundreds of content PEs.
//
// Invoke with one output root; the export goes to <root>/<program name>/ so a
// single analyzeHeadless run can import many files and export each one.
// @category PetzContent
import ghidra.app.script.GhidraScript;
import ghidra.program.model.listing.*;
import java.io.PrintWriter;
import java.nio.file.*;

public class ExportListings extends GhidraScript {
    public void run() throws Exception {
        Path out = Path.of(getScriptArgs()[0], currentProgram.getName());
        Files.createDirectories(out.resolve("functions"));
        int total = 0;
        try (PrintWriter index = new PrintWriter(Files.newBufferedWriter(out.resolve("functions.tsv")))) {
            index.println("address\tname\tbytes\tstatus\tmessage");
            FunctionIterator functions = currentProgram.getFunctionManager().getFunctions(true);
            while (functions.hasNext()) {
                monitor.checkCancelled();
                Function f = functions.next();
                String address = f.getEntryPoint().toString();
                try (PrintWriter asm = new PrintWriter(Files.newBufferedWriter(out.resolve("functions/" + address + ".asm")))) {
                    InstructionIterator instructions = currentProgram.getListing().getInstructions(f.getBody(), true);
                    while (instructions.hasNext()) {
                        Instruction ins = instructions.next();
                        asm.printf("%s  %s%n", ins.getAddress(), ins.toString());
                    }
                }
                index.printf("%s\t%s\t%d\tlisting\t%n", address, f.getName(), f.getBody().getNumAddresses());
                index.flush();
                total++;
                if (total % 500 == 0) println("Exported " + total + " functions");
            }
        }
        Files.writeString(out.resolve("summary.txt"), "discovered=" + total + "\npseudocode=0\nfailed=0\n");
        println("Export complete: " + total + " functions");
    }
}
