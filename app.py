import os, json
from datetime import datetime, timedelta
from functools import wraps
from werkzeug.utils import secure_filename
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, abort, send_from_directory
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from flask_wtf.csrf import CSRFProtect
from sqlalchemy import func, or_
from config import Config
from models import db, User, WasteCategory, Complaint, ComplaintImage, ComplaintStatusHistory, CollectionTeam, TeamAssignment, Notification, PriorityRule

app = Flask(__name__)
app.config.from_object(Config)
os.makedirs(os.path.dirname(app.config["SQLALCHEMY_DATABASE_URI"].replace("sqlite:///", "")), exist_ok=True)
os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
db.init_app(app)
csrf = CSRFProtect(app)
login_manager = LoginManager(app)
login_manager.login_view = "login"
login_manager.login_message_category = "warning"

STATUSES = ["Pending", "Approved", "Assigned", "In Progress", "Collected", "Completed", "Rejected"]
PRIORITIES = ["Critical", "High", "Medium", "Low"]
ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}

@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))

def admin_required(fn):
    @wraps(fn)
    @login_required
    def wrapper(*args, **kwargs):
        if current_user.role != "admin":
            abort(403)
        return fn(*args, **kwargs)
    return wrapper

def citizen_required(fn):
    @wraps(fn)
    @login_required
    def wrapper(*args, **kwargs):
        if current_user.role != "citizen":
            abort(403)
        return fn(*args, **kwargs)
    return wrapper

def priority_for(category_name, severity, quantity, health_risk, address=""):
    """Transparent rule-based priority classifier (0-100), not an ML model."""
    severity_points = {"Low": 10, "Medium": 30, "High": 55, "Critical": 70}
    quantity_points = {"Small": 0, "Medium": 10, "Large": 20}
    score = severity_points.get(severity, 30) + quantity_points.get(quantity, 10)
    reasons = [f"{severity} severity", f"{quantity.lower()} waste quantity"]

    category_text = (category_name or "").lower()
    hazardous_terms = ("industrial", "hazard", "chemical", "medical", "battery", "oil", "toxic")
    if any(word in category_text for word in hazardous_terms):
        score += 20
        reasons.append("hazardous or environmentally risky waste category (+20)")
    elif any(word in category_text for word in ("drain", "sewage", "overflow", "dumping")):
        score += 12
        reasons.append("sanitation or drainage risk (+12)")

    if health_risk:
        score += 25
        reasons.append("public health risk reported (+25)")

    location_text = (address or "").lower()
    sensitive_places = ("school", "hospital", "clinic", "market", "bazar", "bus stand")
    if any(word in location_text for word in sensitive_places):
        score += 10
        reasons.append("near a public/sensitive place (+10)")

    score = max(0, min(100, score))
    priority = "Critical" if score >= 80 else "High" if score >= 60 else "Medium" if score >= 35 else "Low"
    return score, priority, "; ".join(reasons)

def notify(user_id, message, complaint_id=None):
    db.session.add(Notification(user_id=user_id, message=message, complaint_id=complaint_id))

def complaint_query_for_user():
    q = Complaint.query
    if current_user.role != "admin":
        q = q.filter_by(user_id=current_user.id)
    search = request.args.get("q", "").strip()
    status = request.args.get("status", "")
    priority = request.args.get("priority", "")
    category = request.args.get("category", "")
    if search:
        like = f"%{search}%"
        q = q.join(User).join(WasteCategory).filter(or_(Complaint.title.ilike(like), Complaint.address.ilike(like), User.name.ilike(like), WasteCategory.name.ilike(like), func.cast(Complaint.id, db.String).ilike(like)))
    if status: q = q.filter(Complaint.status == status)
    if priority: q = q.filter(Complaint.priority == priority)
    if category:
        try: q = q.filter(Complaint.category_id == int(category))
        except ValueError: pass
    start = request.args.get("start_date")
    end = request.args.get("end_date")
    if start:
        try: q = q.filter(Complaint.created_at >= datetime.strptime(start, "%Y-%m-%d"))
        except ValueError: pass
    if end:
        try: q = q.filter(Complaint.created_at < datetime.strptime(end, "%Y-%m-%d") + timedelta(days=1))
        except ValueError: pass
    return q.order_by(Complaint.created_at.desc())

@app.context_processor
def inject_globals():
    unread = Notification.query.filter_by(user_id=current_user.id, is_read=False).count() if current_user.is_authenticated else 0
    return {"unread_notifications": unread, "STATUSES": STATUSES, "PRIORITIES": PRIORITIES}

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        if not name or not email or len(password) < 8:
            flash("Enter your name, a valid email, and a password of at least 8 characters.", "danger")
        elif User.query.filter_by(email=email).first():
            flash("That email is already registered.", "danger")
        else:
            user = User(name=name, email=email, phone=request.form.get("phone", "").strip(), address=request.form.get("address", "").strip(), role="citizen")
            user.set_password(password)
            db.session.add(user); db.session.commit()
            flash("Registration successful. Please log in.", "success")
            return redirect(url_for("login"))
    return render_template("auth/register.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        user = User.query.filter_by(email=email).first()
        if user and user.check_password(request.form.get("password", "")):
            login_user(user)
            flash(f"Welcome back, {user.name}!", "success")
            return redirect(url_for("admin_dashboard" if user.role == "admin" else "citizen_dashboard"))
        flash("Invalid email or password.", "danger")
    return render_template("auth/login.html")

@app.route("/logout")
@login_required
def logout():
    logout_user()
    flash("You have been logged out.", "info")
    return redirect(url_for("index"))

@app.route("/citizen")
@citizen_required
def citizen_dashboard():
    complaints = Complaint.query.filter_by(user_id=current_user.id).order_by(Complaint.created_at.desc()).limit(5).all()
    counts = {s: Complaint.query.filter_by(user_id=current_user.id, status=s).count() for s in STATUSES}
    return render_template("citizen/dashboard.html", complaints=complaints, counts=counts, total=sum(counts.values()))

@app.route("/complaints/new", methods=["GET", "POST"])
@citizen_required
def new_complaint():
    categories = WasteCategory.query.filter_by(active=True).order_by(WasteCategory.name).all()
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        address = request.form.get("address", "").strip()
        category = db.session.get(WasteCategory, request.form.get("category_id", type=int))
        if not title or not description or not address or not category:
            flash("Please complete all required fields.", "danger")
            return render_template("citizen/new_complaint.html", categories=categories)
        severity = request.form.get("severity", "Medium")
        if severity not in ["Low", "Medium", "High", "Critical"]: severity = "Medium"
        quantity = request.form.get("quantity", "Medium")
        if quantity not in ["Small", "Medium", "Large"]: quantity = "Medium"
        health = request.form.get("health_risk") == "on"
        score, priority, reason = priority_for(category.name, severity, quantity, health, address)
        c = Complaint(title=title, description=description, category=category, user_id=current_user.id,
                      address=address, latitude=request.form.get("latitude", type=float), longitude=request.form.get("longitude", type=float),
                      severity=severity, quantity=quantity, health_risk=health, priority=priority, priority_score=score, priority_reason=reason)
        db.session.add(c); db.session.flush()
        image = request.files.get("photo")
        if image and image.filename:
            ext = image.filename.rsplit(".", 1)[-1].lower() if "." in image.filename else ""
            if ext not in ALLOWED_EXTENSIONS:
                db.session.rollback(); flash("Only JPG, JPEG, PNG, and WEBP images are allowed.", "danger")
                return render_template("citizen/new_complaint.html", categories=categories)
            filename = secure_filename(f"complaint_{c.id}_{int(datetime.utcnow().timestamp())}.{ext}")
            image.save(os.path.join(app.config["UPLOAD_FOLDER"], filename))
            db.session.add(ComplaintImage(complaint_id=c.id, filename=filename))
        db.session.add(ComplaintStatusHistory(complaint_id=c.id, old_status=None, new_status="Pending", note="Complaint submitted", changed_by=current_user.id))
        db.session.commit()
        for admin in User.query.filter_by(role="admin").all():
            notify(admin.id, f"New complaint #{c.id} submitted ({priority} priority).", c.id)
        db.session.commit()
        flash(f"Complaint #{c.id} submitted successfully. Assigned priority: {priority}.", "success")
        return redirect(url_for("my_complaints"))
    return render_template("citizen/new_complaint.html", categories=categories)

@app.route("/complaints")
@login_required
def my_complaints():
    page = request.args.get("page", 1, type=int)
    pagination = complaint_query_for_user().paginate(page=page, per_page=8, error_out=False)
    return render_template("complaints/list.html", pagination=pagination, categories=WasteCategory.query.order_by(WasteCategory.name).all())

def get_visible_complaint(complaint_id):
    c = db.session.get(Complaint, complaint_id)
    if not c: abort(404)
    if current_user.role != "admin" and c.user_id != current_user.id: abort(403)
    return c

@app.route("/complaints/<int:complaint_id>")
@login_required
def complaint_detail(complaint_id):
    c = get_visible_complaint(complaint_id)
    teams = CollectionTeam.query.filter_by(active=True).all() if current_user.role == "admin" else []
    return render_template("complaints/detail.html", complaint=c, teams=teams)

@app.route("/admin")
@admin_required
def admin_dashboard():
    total = Complaint.query.count()
    counts = {s: Complaint.query.filter_by(status=s).count() for s in STATUSES}
    priority_counts = {p: Complaint.query.filter_by(priority=p).count() for p in PRIORITIES}
    category_rows = db.session.query(WasteCategory.name, func.count(Complaint.id)).outerjoin(Complaint).group_by(WasteCategory.id).all()

    # Prepare a rolling 12-month series so the dashboard always has a useful chart.
    today = datetime.utcnow().date()
    first_month = today.replace(day=1)
    for _ in range(11):
        first_month = (first_month - timedelta(days=1)).replace(day=1)
    grouped = db.session.query(
        func.strftime("%Y-%m", Complaint.created_at), Complaint.status, func.count(Complaint.id)
    ).filter(Complaint.created_at >= datetime.combine(first_month, datetime.min.time())).group_by(
        func.strftime("%Y-%m", Complaint.created_at), Complaint.status
    ).all()
    month_data = {}
    for month_key, status_name, amount in grouped:
        month_data.setdefault(month_key, {})[status_name] = amount
    month_labels, monthly_total, monthly_completed, monthly_open = [], [], [], []
    cursor = first_month
    for _ in range(12):
        key = cursor.strftime("%Y-%m")
        values = month_data.get(key, {})
        month_labels.append(cursor.strftime("%b %Y"))
        month_total = sum(values.values())
        month_labels[-1] = cursor.strftime("%b")
        monthly_total.append(month_total)
        monthly_completed.append(values.get("Completed", 0) + values.get("Collected", 0))
        monthly_open.append(month_total - monthly_completed[-1])
        cursor = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)

    total_zones = db.session.query(func.count(func.distinct(Complaint.address))).scalar() or 0
    served_zones = db.session.query(func.count(func.distinct(Complaint.address))).filter(
        Complaint.status.in_(["Collected", "Completed"])
    ).scalar() or 0
    recent = Complaint.query.order_by(Complaint.created_at.desc()).limit(7).all()
    return render_template(
        "admin/dashboard.html", total=total, counts=counts, priority_counts=priority_counts,
        category_rows=category_rows, month_labels=month_labels, monthly_total=monthly_total,
        monthly_completed=monthly_completed, monthly_open=monthly_open, recent=recent,
        team_count=CollectionTeam.query.filter_by(active=True).count(),
        total_zones=total_zones, served_zones=served_zones,
        unserved_zones=max(total_zones - served_zones, 0)
    )

@app.route("/admin/complaints")
@admin_required
def admin_complaints():
    pagination = complaint_query_for_user().paginate(page=request.args.get("page", 1, type=int), per_page=10, error_out=False)
    return render_template("complaints/list.html", pagination=pagination, categories=WasteCategory.query.order_by(WasteCategory.name).all(), admin_view=True)

@app.route("/admin/complaints/<int:complaint_id>/status", methods=["POST"])
@admin_required
def update_status(complaint_id):
    c = db.session.get(Complaint, complaint_id) or abort(404)
    new_status = request.form.get("status")
    if new_status not in STATUSES:
        flash("Invalid status.", "danger")
    else:
        old = c.status
        c.status = new_status
        c.updated_at = datetime.utcnow()
        if new_status == "Completed": c.completed_at = datetime.utcnow()
        db.session.add(ComplaintStatusHistory(complaint_id=c.id, old_status=old, new_status=new_status, note=request.form.get("note", "").strip(), changed_by=current_user.id))
        notify(c.user_id, f"Complaint #{c.id} status changed from {old} to {new_status}.", c.id)
        db.session.commit()
        flash("Complaint status updated and citizen notified.", "success")
    return redirect(url_for("complaint_detail", complaint_id=c.id))

@app.route("/admin/complaints/<int:complaint_id>/priority", methods=["POST"])
@admin_required
def override_priority(complaint_id):
    c = db.session.get(Complaint, complaint_id) or abort(404)
    p = request.form.get("priority")
    if p in PRIORITIES:
        c.priority = p
        c.priority_reason = "Priority manually reviewed by administrator"
        db.session.commit()
        notify(c.user_id, f"Complaint #{c.id} priority was reviewed: {p}.", c.id); db.session.commit()
        flash("Priority updated.", "success")
    return redirect(url_for("complaint_detail", complaint_id=c.id))

@app.route("/admin/teams", methods=["GET", "POST"])
@admin_required
def teams():
    if request.method == "POST":
        name=request.form.get("name","").strip(); leader=request.form.get("leader","").strip(); phone=request.form.get("phone","").strip()
        if name and leader and phone:
            db.session.add(CollectionTeam(name=name, leader=leader, phone=phone, capacity=request.form.get("capacity", 5, type=int)))
            db.session.commit(); flash("Collection team added.", "success")
        else: flash("Complete all team fields.", "danger")
        return redirect(url_for("teams"))
    return render_template("admin/teams.html", teams=CollectionTeam.query.order_by(CollectionTeam.name).all())

@app.route("/admin/teams/<int:team_id>/toggle", methods=["POST"])
@admin_required
def toggle_team(team_id):
    team=db.session.get(CollectionTeam, team_id) or abort(404); team.active=not team.active; db.session.commit()
    flash("Team availability updated.", "success"); return redirect(url_for("teams"))

@app.route("/admin/complaints/<int:complaint_id>/assign", methods=["POST"])
@admin_required
def assign_team(complaint_id):
    c=db.session.get(Complaint, complaint_id) or abort(404)
    team=db.session.get(CollectionTeam, request.form.get("team_id", type=int))
    if not team or not team.active:
        flash("Choose an active team.", "danger")
    else:
        db.session.add(TeamAssignment(complaint_id=c.id, team_id=team.id, assigned_by=current_user.id))
        old=c.status; c.status="Assigned"
        db.session.add(ComplaintStatusHistory(complaint_id=c.id, old_status=old, new_status="Assigned", note=f"Assigned to {team.name}", changed_by=current_user.id))
        notify(c.user_id, f"Collection team {team.name} has been assigned to complaint #{c.id}.", c.id)
        db.session.commit(); flash("Collection team assigned.", "success")
    return redirect(url_for("complaint_detail", complaint_id=c.id))

@app.route("/admin/categories", methods=["GET", "POST"])
@admin_required
def categories():
    if request.method == "POST":
        name=request.form.get("name","").strip()
        if name and not WasteCategory.query.filter_by(name=name).first():
            db.session.add(WasteCategory(name=name, description=request.form.get("description","").strip())); db.session.commit(); flash("Category added.", "success")
        else: flash("Enter a unique category name.", "danger")
        return redirect(url_for("categories"))
    return render_template("admin/categories.html", categories=WasteCategory.query.order_by(WasteCategory.name).all())

@app.route("/admin/categories/<int:category_id>/toggle", methods=["POST"])
@admin_required
def toggle_category(category_id):
    cat=db.session.get(WasteCategory, category_id) or abort(404); cat.active=not cat.active; db.session.commit()
    flash("Category updated.", "success"); return redirect(url_for("categories"))

@app.route("/admin/citizens")
@admin_required
def citizens():
    return render_template("admin/citizens.html", citizens=User.query.filter_by(role="citizen").order_by(User.created_at.desc()).all())

@app.route("/admin/statistics")
@admin_required
def statistics():
    total=Complaint.query.count()
    completed=Complaint.query.filter_by(status="Completed").count()
    pending=Complaint.query.filter_by(status="Pending").count()
    durations=[]
    for c in Complaint.query.filter(Complaint.completed_at.isnot(None)).all():
        durations.append((c.completed_at-c.created_at).total_seconds()/3600)
    avg=round(sum(durations)/len(durations),1) if durations else 0
    team_rows=[]
    for team in CollectionTeam.query.all():
        ids=[a.complaint_id for a in team.assignments]
        team_rows.append({"name":team.name,"assigned":len(ids),"completed":Complaint.query.filter(Complaint.id.in_(ids), Complaint.status=="Completed").count() if ids else 0})
    return render_template("admin/statistics.html", total=total, completed=completed, pending=pending, avg=avg, team_rows=team_rows)

@app.route("/admin/map")
@admin_required
def waste_map():
    return render_template("admin/map.html")

@app.route("/api/complaints/map")
@admin_required
def map_data():
    rows=[]
    for c in Complaint.query.filter(Complaint.latitude.isnot(None), Complaint.longitude.isnot(None)).all():
        rows.append({"id":c.id,"title":c.title,"category":c.category.name,"address":c.address,"lat":c.latitude,"lng":c.longitude,"priority":c.priority,"status":c.status,"url":url_for("complaint_detail", complaint_id=c.id)})
    return jsonify(rows)

@app.route("/notifications")
@login_required
def notifications():
    items=Notification.query.filter_by(user_id=current_user.id).order_by(Notification.created_at.desc()).all()
    for n in items: n.is_read=True
    db.session.commit()
    return render_template("notifications.html", items=items)

@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    if request.method=="POST":
        current_user.name=request.form.get("name","").strip()
        current_user.phone=request.form.get("phone","").strip()
        current_user.address=request.form.get("address","").strip()
        db.session.commit(); flash("Profile updated.", "success"); return redirect(url_for("profile"))
    return render_template("profile.html")

@app.route("/uploads/complaints/<path:filename>")
@login_required
def uploaded_file(filename):
    return send_from_directory(app.config["UPLOAD_FOLDER"], filename)

@app.errorhandler(403)
def forbidden(e): return render_template("error.html", code=403, message="You do not have permission to access this page."), 403
@app.errorhandler(404)
def not_found(e): return render_template("error.html", code=404, message="The requested page could not be found."), 404
@app.errorhandler(413)
def too_large(e): return render_template("error.html", code=413, message="Image is too large. Maximum upload size is 5 MB."), 413

def initialize_database():
    with app.app_context():
        db.create_all()
        defaults=["Overflowing Garbage Bin","Illegal Waste Dumping","Uncollected Household Waste","Industrial Waste","Plastic Waste","Drainage Waste","Construction Waste","Other"]
        for name in defaults:
            if not WasteCategory.query.filter_by(name=name).first(): db.session.add(WasteCategory(name=name))
        if not User.query.filter_by(email="admin@smartwaste.local").first():
            admin=User(name="System Administrator",email="admin@smartwaste.local",phone="01700000000",address="Municipal Office",role="admin")
            admin.set_password("Admin@123"); db.session.add(admin)
        if CollectionTeam.query.count()==0:
            db.session.add(CollectionTeam(name="Green City Team A",leader="Rahim Uddin",phone="01711111111",capacity=8))
            db.session.add(CollectionTeam(name="Clean City Team B",leader="Karim Hasan",phone="01722222222",capacity=6))
        if PriorityRule.query.count()==0:
            db.session.add_all([
                PriorityRule(name="Public health risk", weight=25, description="Raises priority when citizens report health risks"),
                PriorityRule(name="Hazardous waste", weight=20, description="Raises priority for industrial, chemical, medical or toxic waste"),
                PriorityRule(name="Large quantity", weight=20, description="Raises priority for large waste accumulation"),
                PriorityRule(name="Sensitive location", weight=10, description="Raises priority near schools, hospitals and markets")
            ])
        db.session.commit()

        # Populate a first-run demo dataset so the admin dashboard and map are useful immediately.
        # These are fictional sample reports for demonstration, not verified real incidents.
        demo_user = User.query.filter_by(email="demo.citizen@smartwaste.local").first()
        if not demo_user:
            demo_user = User(name="Demo Citizen", email="demo.citizen@smartwaste.local",
                             phone="01700000001", address="Savar, Dhaka", role="citizen")
            demo_user.set_password("Citizen@123")
            db.session.add(demo_user)
            db.session.flush()

        if Complaint.query.filter_by(user_id=demo_user.id).count() == 0:
            team_list = CollectionTeam.query.filter_by(active=True).order_by(CollectionTeam.id).all()
            category_by_name = {c.name: c for c in WasteCategory.query.all()}
            demo_locations = [
                ("Savar Bazar", 23.8436, 90.2570), ("Nabinagar", 23.8855, 90.2640),
                ("Radio Colony", 23.8469, 90.2576), ("Hemayetpur", 23.7900, 90.2790),
                ("Ashulia", 23.9160, 90.3030), ("Bank Town", 23.8480, 90.2630),
                ("Genda", 23.8460, 90.2690), ("Baipayl", 23.9540, 90.2690),
                ("Thana Road", 23.8448, 90.2562), ("Savar Bus Stand", 23.8430, 90.2550),
                ("Bazar Road", 23.8427, 90.2581), ("Radio Colony School Area", 23.8490, 90.2600),
                ("Enam Medical Area", 23.8475, 90.2578), ("Amin Bazar", 23.7840, 90.3440),
                ("Dhamsona", 23.9340, 90.2850), ("Jamgora", 23.9300, 90.2910),
                ("Kashimpur", 23.9950, 90.2670), ("Zirabo", 23.9270, 90.3230),
                ("Kathgara", 23.9250, 90.3150), ("DEPZ Gate", 23.9610, 90.2670),
                ("Kaundia", 23.8190, 90.3020), ("Tetuljhora", 23.8060, 90.2880),
                ("Pathalia", 23.8830, 90.3000), ("Bongaon", 23.8650, 90.2920),
                ("Birulia", 23.9000, 90.3500), ("Yearpur", 23.9460, 90.3080),
                ("Ashulia Bazar", 23.9170, 90.3000), ("C&B Bus Stand", 23.8450, 90.2600)
            ]
            demo_specs = [
                ("Overflowing community bin", "Overflowing Garbage Bin", "High", "Large", True, "Completed"),
                ("Illegal roadside dumping", "Illegal Waste Dumping", "High", "Large", False, "Completed"),
                ("Household waste not collected", "Uncollected Household Waste", "Medium", "Medium", False, "Completed"),
                ("Industrial waste near road", "Industrial Waste", "Critical", "Large", True, "Completed"),
                ("Plastic waste accumulation", "Plastic Waste", "Medium", "Large", False, "Collected"),
                ("Blocked drainage with waste", "Drainage Waste", "High", "Large", True, "Completed"),
                ("Construction debris pile", "Construction Waste", "Medium", "Large", False, "Completed"),
                ("Overflowing public bin", "Overflowing Garbage Bin", "High", "Large", True, "Collected"),
                ("Uncollected market waste", "Uncollected Household Waste", "High", "Large", True, "Pending"),
                ("Mixed waste near bus stand", "Overflowing Garbage Bin", "Medium", "Large", False, "Assigned"),
                ("Plastic dumping beside road", "Plastic Waste", "Medium", "Medium", False, "Pending"),
                ("Waste near school boundary", "Illegal Waste Dumping", "High", "Medium", True, "In Progress"),
                ("Medical-area mixed waste", "Industrial Waste", "Critical", "Medium", True, "Pending"),
                ("Drainage waste accumulation", "Drainage Waste", "High", "Large", True, "Pending"),
                ("Household garbage pile", "Uncollected Household Waste", "Medium", "Large", False, "Assigned"),
                ("Plastic bags blocking drain", "Drainage Waste", "High", "Medium", True, "Pending"),
                ("Industrial dumping complaint", "Industrial Waste", "Critical", "Large", True, "In Progress"),
                ("Overflowing roadside bin", "Overflowing Garbage Bin", "High", "Large", False, "Pending"),
                ("Construction waste left behind", "Construction Waste", "Medium", "Large", False, "Pending"),
                ("Uncollected household waste", "Uncollected Household Waste", "Medium", "Medium", False, "Pending"),
                ("Illegal dumping near canal", "Illegal Waste Dumping", "High", "Large", True, "Pending"),
                ("Mixed waste pile", "Other", "Low", "Medium", False, "Pending"),
                ("Blocked drain with garbage", "Drainage Waste", "High", "Large", True, "In Progress"),
                ("Plastic waste at roadside", "Plastic Waste", "Medium", "Large", False, "Pending"),
                ("Overflowing bin near homes", "Overflowing Garbage Bin", "Medium", "Medium", False, "Pending"),
                ("Industrial waste concern", "Industrial Waste", "Critical", "Large", True, "Pending"),
                ("Uncollected market garbage", "Uncollected Household Waste", "High", "Large", True, "Assigned"),
                ("Waste beside transport stop", "Illegal Waste Dumping", "Medium", "Large", False, "Pending")
            ]
            now = datetime.utcnow()
            for index, (location, lat, lng) in enumerate(demo_locations):
                title, category_name, severity, quantity, health, status = demo_specs[index]
                category = category_by_name[category_name]
                address = f"{location}, Savar, Dhaka"
                score, priority, reason = priority_for(category.name, severity, quantity, health, address)
                created = now - timedelta(days=(index * 11) % 330)
                complaint = Complaint(
                    title=title, description=("DEMO SAMPLE DATA — fictional report created to demonstrate the dashboard, map, and priority classifier. "
                                              f"Reported issue: {title.lower()}."),
                    category=category, user_id=demo_user.id, address=address, latitude=lat, longitude=lng,
                    severity=severity, quantity=quantity, health_risk=health, priority=priority,
                    priority_score=score, priority_reason=reason, status=status,
                    created_at=created, updated_at=created,
                    completed_at=created + timedelta(days=1) if status in ("Collected", "Completed") else None
                )
                db.session.add(complaint)
                db.session.flush()
                db.session.add(ComplaintStatusHistory(complaint_id=complaint.id, old_status=None,
                    new_status=status, note="Demo sample report" if status == "Pending" else f"Demo sample status: {status}",
                    changed_by=demo_user.id, created_at=created))
                if status in ("Assigned", "In Progress") and team_list:
                    selected_team = team_list[index % len(team_list)]
                    db.session.add(TeamAssignment(complaint_id=complaint.id, team_id=selected_team.id,
                        assigned_by=User.query.filter_by(role="admin").first().id, assigned_at=created))
            db.session.commit()

if __name__ == "__main__":
    initialize_database()
    app.run(debug=True)
