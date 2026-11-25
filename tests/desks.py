"""Desks of the synced tree: desk_for, tree_folders and desk_folder on a scratch ResilioSync tree.

Since 2026-11-25 the projects below Projects may sit in grouping folders without a date stamp (the categories, done,
failed, iOS Apps), any number of levels deep, and move between them; a desk below Projects is the first date-stamped
folder, and the desk names of projects that sit directly in Projects stay as before. This builds a mixed tree (projects
directly in Projects, in a category, in done, in a category's done) and checks the desk of each folder, the folder of
each desk, a '.desk' override that moves with its folder, and a desk name that is two folders. Runs on Windows and macOS,
touches nothing outside a temporary folder.

    python -B tests/desks.py [path to mail.py]    (default: the live one beside this folder)
"""
import importlib.util, shutil, sys, tempfile
from pathlib import Path

sys.dont_write_bytecode = True
SCRIPT = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent / "mail.py").resolve()
spec = importlib.util.spec_from_file_location("mail_under_test", SCRIPT)
mail = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mail)
scratch = Path(tempfile.mkdtemp(prefix="claudemail-desks-"))
root = scratch / "ResilioSync"
mail.SYNC_ROOT = root
mail.MAILBOX_ROOT = root / "Services" / "ClaudeMail" / "mail"

failures = []


def expect(label: str, got, wanted) -> None:
    ok = got == wanted
    print(("PASS " if ok else "FAIL ") + label + ("" if ok else "  got " + repr(got) + ", wanted " + repr(wanted)))
    if not ok:
        failures.append(label)


def make(relative: str) -> Path:
    path = root / relative
    path.mkdir(parents=True, exist_ok=True)
    return path


for relative in ["Projects/260924 ClaudeMail/sub", "Projects/260704 Garden Plan/261024 Actions",
                 "Projects/Utilities/260925 Weather Station/src", "Projects/done/141218 Old Website/Notes",
                 "Projects/Utilities/done/260101 Deep project", "Projects/failed/250520 Label Trainer",
                 "Projects/iOS Apps/260907 Reader App/App", "Projects/Household/261001 Twin",
                 "Projects/done/261002 Twin", "Services/260926 Backup Service/src", "Services/ClaudeMail/mail",
                 "Tools/QueryTool", "Notes/Kitchen"]:
    make(relative)
(root / "Projects" / "iOS Apps" / ".desk").write_text("iOS Apps\n", encoding="utf-8")

try:
    desks = [("Projects/260924 ClaudeMail/sub", "ClaudeMail"),                       # a project directly in Projects
             ("Projects/260704 Garden Plan/261024 Actions", "Garden Plan"),   # the first stamp wins
             ("Projects/Utilities/260925 Weather Station/src", "Weather Station"),   # category level
             ("Projects/done/141218 Old Website/Notes", "Old Website"),   # done level
             ("Projects/Utilities/done/260101 Deep project", "Deep project"),  # two grouping levels
             ("Projects/failed/250520 Label Trainer", "Label Trainer"),
             ("Projects/iOS Apps/260907 Reader App/App", "iOS Apps"),          # the '.desk' override
             ("Projects/Utilities", "Utilities"),                         # a grouping folder itself
             ("Projects/done", "done"),
             ("Projects", "Home"),
             ("Services/260926 Backup Service/src", "Backup Service"),              # Services and Tools: unchanged
             ("Services/ClaudeMail/mail", "ClaudeMail"),
             ("Services", "Home"),
             ("Tools/QueryTool", "QueryTool"),
             ("Notes/Kitchen", "Notes"),
             ("", "Home")]
    for relative, wanted in desks:
        expect("desk_for " + (relative or "the sync root"), mail.desk_for(str(root / relative)), wanted)
    # A folder named in another machine's report, which does not exist here: the same rule from its text alone.
    expect("desk_for a Mac path in a category", mail.desk_for("/Users/someone/ResilioSync/Projects/Household/261001 Trip Planner/x"), "Trip Planner")
    expect("desk_for a Windows path in done", mail.desk_for("C:" + chr(92) + "Users" + chr(92) + "someone" + chr(92) + "ResilioSync"
                                                            + chr(92) + "Projects" + chr(92) + "done" + chr(92) + "260818 Photo Sorter"), "Photo Sorter")
    expect("desk_for a folder outside the tree", mail.desk_for(str(scratch / "Developer" / "Foo")), "Home")

    def tree(desk: str) -> list:
        return sorted(Path(p).relative_to(root).as_posix() for p in mail.tree_folders(desk))

    expect("tree_folders, project directly in Projects (and a service of that name, as in the real tree)", tree("ClaudeMail"),
           ["Projects/260924 ClaudeMail", "Services/ClaudeMail"])
    expect("tree_folders, category level", tree("Weather Station"), ["Projects/Utilities/260925 Weather Station"])
    expect("tree_folders, done level", tree("old website"), ["Projects/done/141218 Old Website"])
    expect("tree_folders, two grouping levels", tree("Deep project"), ["Projects/Utilities/done/260101 Deep project"])
    expect("tree_folders, nothing inside a project", tree("Actions"), [])
    expect("tree_folders, a folder whose '.desk' names another desk", tree("Reader App"), [])
    expect("tree_folders, the '.desk' desk", tree("iOS Apps"), ["Projects/iOS Apps"])
    expect("tree_folders, a grouping folder", tree("done"), ["Projects/done"])
    expect("tree_folders, Services", tree("Backup Service"), ["Services/260926 Backup Service"])
    expect("tree_folders, elsewhere in the tree", tree("Notes"), ["Notes"])
    expect("tree_folders, two folders of one desk", tree("Twin"), ["Projects/Household/261001 Twin", "Projects/done/261002 Twin"])

    folder, why = mail.desk_folder("Weather Station", {})
    expect("desk_folder, category level", (Path(folder).relative_to(root).as_posix(), why), ("Projects/Utilities/260925 Weather Station", ""))
    folder, why = mail.desk_folder("Old Website", {})
    expect("desk_folder, done level", (Path(folder).relative_to(root).as_posix(), why), ("Projects/done/141218 Old Website", ""))
    folder, why = mail.desk_folder("Twin", {})
    expect("desk_folder, two folders are not guessed between", (folder, "more than one folder" in why), ("", True))

    # The move: iOS Apps into a category and a project from Projects into one, the '.desk' file travelling along.
    shutil.move(str(root / "Projects" / "iOS Apps"), str(make("Projects/Hobbies") / "iOS Apps"))
    shutil.move(str(root / "Projects" / "260924 ClaudeMail"), str(make("Projects/Utilities") / "260924 ClaudeMail"))
    expect("after the move, desk_for a moved project", mail.desk_for(str(root / "Projects/Utilities/260924 ClaudeMail/sub")), "ClaudeMail")
    expect("after the move, desk_for an app below a moved iOS Apps",
           mail.desk_for(str(root / "Projects/Hobbies/iOS Apps/260907 Reader App/App")), "iOS Apps")
    expect("after the move, tree_folders of a moved project", tree("ClaudeMail"),
           ["Projects/Utilities/260924 ClaudeMail", "Services/ClaudeMail"])
    expect("after the move, tree_folders of the '.desk' desk", tree("iOS Apps"), ["Projects/Hobbies/iOS Apps"])
finally:
    shutil.rmtree(scratch)

print(("FAILED: " + ", ".join(failures)) if failures else "all passed")
sys.exit(1 if failures else 0)
