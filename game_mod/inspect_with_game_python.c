/* Developer-only offline runner; shares the compiler's verified wchar ABI. */
#define main plumbmate_compiler_unused_main
#include "compile_with_game_python.c"
#undef main

int main(int argc, char **argv) {
    if (argc != 4) {
        fprintf(stderr, "usage: %s LIBPYTHON STDLIB_PATH SCRIPT\n", argv[0]);
        return 2;
    }
    void *library = dlopen(argv[1], RTLD_NOW | RTLD_LOCAL);
    if (library == NULL) { fprintf(stderr, "%s\n", dlerror()); return 3; }
    py_set_path_fn set_path = (py_set_path_fn)required_symbol(library, "Py_SetPath");
    py_initialize_fn initialize = (py_initialize_fn)required_symbol(library, "Py_Initialize");
    py_run_simple_string_flags_fn run = (py_run_simple_string_flags_fn)required_symbol(library, "PyRun_SimpleStringFlags");
    py_finalize_ex_fn finalize = (py_finalize_ex_fn)required_symbol(library, "Py_FinalizeEx");
    py_wchar_t *path = utf8_to_python_wide(argv[2]);
    if (path == NULL) return 4;
    if (setenv("PLUMBMATE_INSPECT_SCRIPT", argv[3], 1) != 0) return 4;
    set_path(path);
    initialize();
    int result = run("import os\nexec(compile(open(os.environ['PLUMBMATE_INSPECT_SCRIPT'], encoding='utf-8').read(), os.environ['PLUMBMATE_INSPECT_SCRIPT'], 'exec'))", NULL);
    int finished = finalize();
    free(path);
    dlclose(library);
    return result == 0 && finished == 0 ? 0 : 5;
}
